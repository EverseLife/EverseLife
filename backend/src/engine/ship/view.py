# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: what the client shows before the attempt.

Split out of `engine/ship.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import travel, world
from src.engine.ship._base import SPACEPORT
from src.engine.ship.belonging import crew_of, is_aboard, nodes_of, of_node
from src.engine.ship.physics import (
    _things,
    base_hours,
    engine_class,
    engines,
    fuel_aboard,
    fuel_for,
    life_support,
    mass,
    mass_parts,
    passage_hours,
    route_class,
    thrust,
)
from src.models.inventory import Container, ContainerKind, Item
from src.models.job import Job, JobKind, JobState
from src.models.ship import Ship
from src.models.world import Edge, Node, Planet
from src.units import (
    ROUND_HOURS,
    ROUND_MASS,
    ROUND_RATIO,
)


async def in_sight(
    session: AsyncSession, constants: Constants, node: Node
) -> dict[str, list[dict[str, object]]] | None:
    """What of ships is visible from this node, and nothing beyond it.

    A ship's interior is **not on the public map** (D-201). From the pier a ship
    is one hull, and how many cabins it holds, what is joined to what and where
    the hold is stays unknown -- that is the whole point of the single
    connector: nothing is seen past the gangway. So what a ship shows travels
    with the look of whoever stands close enough, and only what they may see:

    * **at a spaceport** -- the ships moored to it, each as one node with its
      gangway. That is the door, not the inside: it appears on walking up to the
      pier and is gone on walking away from it;
    * **aboard** -- the rooms and the ways between them, because from inside a
      ship is an ordinary piece of the graph one walks around.

    None means neither: ordinary ground with no ship within sight.
    """
    if is_aboard(node):
        return await _from_aboard(session, constants, node)
    return await _from_pier(session, constants, node)


async def _from_pier(
    session: AsyncSession, constants: Constants, port: Node
) -> dict[str, list[dict[str, object]]] | None:
    """Ships moored here: a door apiece, on the layer the pier itself is on.

    A moored ship stands in the city as a building does -- one walks up to it
    and up its gangway -- so that is where the map shows it, under the same city
    as the port. Its own layer stays what it is; this is the delegate's trick
    the map has used from the start (D-045), not a second kind of node.
    """
    moored = (
        (await session.execute(select(Ship).where(Ship.docked_node_id == port.id))).scalars().all()
    )
    if not moored:
        return None

    city = None if port.parent_id is None else await session.get(Node, port.parent_id)
    nodes: list[dict[str, object]] = []
    edges: list[dict[str, object]] = []
    for ship in moored:
        connector = await session.get(Node, ship.connector_node_id)
        if connector is None:  # pragma: no cover -- a ship always has one
            continue
        nodes.append(
            {
                "key": connector.key,
                #: The ship's name, not the compartment's: from the pier one
                #: sees «Заря», and what its first room is called is a thing
                #: learnt aboard.
                "name": ship.name,
                "layer": port.layer.value,
                "parent": None if city is None else city.key,
                "ring": (port.properties or {}).get("кольцо"),
                "exit": False,
                "port": False,
                "planet": connector.planet.value,
                "orbit": None,
                "deferred": False,
                "aboard": True,
                "flight": None,
            }
        )
        gangway = await travel._edge_between(session, port.id, connector.id)
        if gangway is not None:
            edges.append(
                {
                    "a": port.key,
                    "b": connector.key,
                    "surface": gangway.surface.value,
                    "seconds": round(travel.edge_seconds(constants, gangway)),
                }
            )
    return {"nodes": nodes, "edges": edges} if nodes else None


async def _from_aboard(
    session: AsyncSession, constants: Constants, node: Node
) -> dict[str, list[dict[str, object]]] | None:
    """The ship one is standing in: its rooms and the ways between them."""
    ship = await of_node(session, node)
    if ship is None:  # pragma: no cover -- an aboard node always has its ship
        return None

    rooms = await nodes_of(session, ship)
    delegate = await session.get(Node, ship.node_id)
    keys = {room.id: room.key for room in rooms}
    if delegate is not None:
        keys[delegate.id] = delegate.key
    #: The gangway too, when there is one: from inside the way out is a fact of
    #: the graph like any other, and without it the interior hangs on nothing.
    port = None if ship.docked_node_id is None else await session.get(Node, ship.docked_node_id)
    if port is not None:
        keys[port.id] = port.key

    ways = (
        (
            await session.execute(
                select(Edge).where(or_(Edge.node_a_id.in_(keys), Edge.node_b_id.in_(keys)))
            )
        )
        .scalars()
        .all()
    )
    return {
        "nodes": [
            {
                "key": room.key,
                "name": room.name,
                "layer": room.layer.value,
                "parent": None if delegate is None else delegate.key,
                "ring": None,
                "exit": False,
                "port": False,
                "planet": room.planet.value,
                "orbit": None,
                "deferred": False,
                "aboard": True,
                "flight": None,
            }
            for room in rooms
        ],
        "edges": [
            {
                "a": keys[edge.node_a_id],
                "b": keys[edge.node_b_id],
                "surface": edge.surface.value,
                "seconds": round(travel.edge_seconds(constants, edge)),
            }
            for edge in ways
            if edge.node_a_id in keys and edge.node_b_id in keys
        ],
    }


async def passages(session: AsyncSession) -> dict[uuid.UUID, dict[str, object]]:
    """Ships under way: the delegate node -> where it goes and when it arrives.

    A ship in flight is **nowhere in the graph**: undocking removes its only
    edge (D-201), so its place cannot be read off the map the way everything
    else can. It is known to one thing only -- the job that will bring the ship
    in -- and that job holds both ends of the passage: it was created at
    departure and fires on arrival. The share between the two is exactly how
    far the ship has got, and that is what the space layer draws.

    Keyed by the delegate node, because that is what the map speaks in.
    """
    flights = (
        (
            await session.execute(
                select(Job).where(Job.kind == JobKind.SHIP_FLIGHT, Job.state == JobState.PENDING)
            )
        )
        .scalars()
        .all()
    )
    if not flights:
        return {}

    afloat = {
        str(ship.id): ship
        for ship in (await session.execute(select(Ship).where(Ship.docked_node_id.is_(None))))
        .scalars()
        .all()
    }
    under_way: dict[uuid.UUID, dict[str, object]] = {}
    for job in flights:
        ship = afloat.get(str(job.payload.get("ship")))
        if ship is None:
            continue
        under_way[ship.node_id] = {
            "to": uuid.UUID(str(job.payload["to"])),
            "started_at": job.created_at,
            "arrives_at": job.run_at,
        }
    return under_way


async def ports(session: AsyncSession) -> list[Node]:
    """Every node with a spaceport. What a ship may aim at at all."""
    return list(
        (
            await session.execute(
                select(Node)
                .join(Container, Container.owner_id == Node.id)
                .join(Item, Item.container_id == Container.id)
                .where(
                    Container.kind == ContainerKind.NODE,
                    Item.type_key.in_(world.station_names(SPACEPORT)),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )


async def profile(
    session: AsyncSession, constants: Constants, catalog: Catalog, ship: Ship
) -> dict:
    """The ship's summary: thrust, mass, and the price of every route from here.

    Shown **before** undocking, deliberately (D-202): a refusal by mass must not
    be a surprise sprung after the hold is loaded. Remote, like every reading:
    information travels the Net, matter requires presence (D-044).
    """
    nodes = await nodes_of(session, ship)
    #: The hold is read once and asked every question: seven readings of the
    #: same rooms were the price of the summary before (review 2026-08-23).
    things = await _things(session, ship)
    weight = await mass(session, constants, catalog, ship, things=things)
    pull = await thrust(session, constants, ship, things=things)
    thrust_ratio = pull / weight if weight > 0 else 0.0
    have_class = await engine_class(session, constants, ship, things=things)
    crew = len(await crew_of(session, ship))
    connector = await session.get(Node, ship.connector_node_id)
    docked = None if ship.docked_node_id is None else await session.get(Node, ship.docked_node_id)

    #: The prices are for **this** moment: the sky turns, and a route quoted an
    #: hour ago is not the route one gets. The player sees what casting off now
    #: would cost, and the window they may prefer to wait for is on the map.
    #: The sky is asked **once per planet**, not once per port: a planet with
    #: hundreds of spaceports (Aurora, D-230) has one distance, not hundreds.
    moment = datetime.now(UTC)
    planet = Planet.TERRA if connector is None else connector.planet
    tables: dict[Planet, float | None] = {}
    routes: list[dict] = []
    for port in await ports(session):
        if docked is not None and port.id == docked.id:
            continue
        if port.planet not in tables:
            tables[port.planet] = await base_hours(
                session, constants, planet, port.planet, at=moment
            )
        table = tables[port.planet]
        if table is None:
            continue
        need_class = route_class(constants, planet, port.planet)
        hours = passage_hours(constants, table, thrust_ratio) if thrust_ratio > 0 else None
        routes.append(
            {
                "node": port.key,
                "name": port.name,
                "planet": port.planet.value,
                "class": need_class,
                "hours": None if hours is None else round(hours, ROUND_HOURS),
                "fuel": (
                    None if hours is None else round(fuel_for(constants, weight, hours), ROUND_MASS)
                ),
                #: Why exactly it is unavailable -- the class or the thrust. A
                #: bare "unavailable" leaves nothing to act on.
                "reachable": (
                    hours is not None
                    and have_class is not None
                    and have_class >= need_class
                    and thrust_ratio >= constants[R.SHIP_MIN_THRUST_RATIO]
                ),
            }
        )

    return {
        "ship": str(ship.id),
        "name": ship.name,
        "nodes": len(nodes),
        "mass": round(weight, ROUND_MASS),
        #: Where the mass comes from and what pushes it (D-230): the console
        #: shows what to cut and what to add, not just the two totals.
        "mass_parts": {
            part: round(value, ROUND_MASS)
            for part, value in (
                await mass_parts(session, constants, catalog, ship, things=things)
            ).items()
        },
        "engines": await engines(session, constants, ship, things=things),
        "thrust": round(pull, ROUND_MASS),
        "ratio": round(thrust_ratio, ROUND_RATIO),
        "min_ratio": constants[R.SHIP_MIN_THRUST_RATIO],
        "class": have_class,
        "crew": crew,
        "life_support": await life_support(session, constants, ship, things=things),
        "fuel": round(await fuel_aboard(session, ship), ROUND_MASS),
        "docked": None if docked is None else docked.key,
        "port": None if docked is None else docked.name,
        #: Which berth of that port, and therefore how long the gangway is: a
        #: busy yard boards you further from the door (D-201).
        "berth": ship.berth,
        "connector": None if connector is None else connector.key,
        "routes": sorted(routes, key=lambda route: (not route["reachable"], route["name"])),
    }
