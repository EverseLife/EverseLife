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
from src.db.base import remember
from src.engine import travel, world
from src.engine.ship._base import OPEN_LANDING, SPACEPORT
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
    thrust,
)
from src.models.inventory import Container, ContainerKind, Item
from src.models.job import Job, JobKind, JobState
from src.models.ship import Ship
from src.models.world import Edge, Layer, Node, Planet
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


async def beacon_lit(session: AsyncSession, constants: Constants, port: Node) -> bool:
    """Whether the port's beacon shines -- whether a ship may aim at it at all.

    A spaceport works while its node is warm and its yard has power (D-231,
    D-232). On a planet where nothing freezes the question does not arise: a
    port there is a port, and an unpaid bill is the meter's business, not the
    sky's.

    This is the rule the whole of Aurora hangs on. **Its blackout is
    irreversible**: let the last working spaceport of the planet go out and
    there is nowhere left to land -- the planet is lost, and the world does not
    insure anybody against it. A brazier is not enough to relight one: warmth
    it gives, power it does not, and power has to be walked in.
    """
    #: Lazy: `ship` is imported by `frost` (a node aboard is always warm), and
    #: `energy` comes along on the same line for the same reason.
    from src.engine import energy, frost  # noqa: PLC0415 -- lazy: breaks the cycle with frost

    if await frost.climate_of(session, port) != frost.FROST:
        return True
    #: Power first: it is one reading per **city**, remembered for the command,
    #: while warmth is three per node and asks about the neighbours. A dark city
    #: is the common case on a planet of dead cities, and it must cost one query.
    return await energy.powered(session, constants, port) and await frost.is_warm(
        session, constants, port
    )


async def lit_ports(session: AsyncSession, constants: Constants) -> list[Node]:
    """The ports a ship may actually reach: the ones whose beacon shines.

    Every city a scout finds beyond the ice comes with a pier of its own
    (`ruins.lost_city`), and every one of them is dark: the list of ports grows
    with play, and the dark ones must not cost a reading each. So the cheap half
    of the answer -- has this city any energy at all -- is asked for **all**
    cities in one query, and only the ports that pass it are asked the dear
    question about warmth.
    """
    from src.engine import energy, frost  # noqa: PLC0415 -- lazy: breaks the cycle with frost

    every = await landings(session)
    if not every:  # pragma: no cover -- there is always the capital's pier
        return []
    powered = await energy.cities_with_power(session, constants)
    found = []
    for port in every:
        #: Bare ground has no beacon to light: on a planet one lands anywhere
        #: on, every surface node is a landing site and always was (D-233).
        if await frost.climate_of(session, port) != frost.FROST:
            found.append(port)
            continue
        if port.parent_id not in powered:
            continue
        if await frost.is_warm(session, constants, port):
            found.append(port)
    return found


async def landings(session: AsyncSession) -> list[Node]:
    """Everywhere a ship may aim at: the yards, and the bare ground of planets
    that take a landing anywhere (D-233).

    A dark port is here too -- being dark makes a place no less a place; what it
    is not is a destination (`lit_ports`).
    """
    return [*await ports(session), *await open_landings(session)]


async def ports(session: AsyncSession) -> list[Node]:
    """Nodes with a **yard** in them, and only those.

    The map draws its piers off this list (`routes.public`), so bare ground a
    ship may set down on does not belong here: Pyroxis has no spaceport and
    cannot have one (D-233), and a legend calling every black field a spaceport
    would be a lie told six times over.
    """
    with_yard = (
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
    return list(with_yard)


async def open_landings(session: AsyncSession) -> list[Node]:
    """The surface nodes of planets one lands anywhere on (D-233).

    Every node of the surface, and the surface only: the planet's own node is
    where it stands in the sky, not a place to put a hull down on. A node
    aboard a ship is not ground either, however low the ship flies.

    A list, and it grows with the planet: every field a scout opens on Pyroxis
    is another destination. D-233 wants the console to answer with the
    **planet** instead, and the node to be chosen after the course is set --
    which is a gesture the client does not have yet. The split is here already
    (`ports` is yards and nothing else, so the map's piers are not lied to);
    what is left is interface work, and it is named in the roadmap as such.
    """
    planets = await _open_planets(session)
    if not planets:
        return []
    found = (
        (
            await session.execute(
                select(Node).where(
                    Node.planet.in_([planet.value for planet in planets]),
                    Node.layer != Layer.SPACE,
                )
            )
        )
        .scalars()
        .all()
    )
    return [node for node in found if not is_aboard(node)]


async def lands_anywhere(session: AsyncSession, node: Node) -> bool:
    """Whether a ship may set down **on this node**.

    The planet's property and the node's own nature, both: the sphere of a
    planet is where it stands in the sky, not ground to put a hull on, and a
    room aboard another ship is not ground either. `open_landings` says the
    same thing about the whole planet at once, and the two must not disagree --
    a flight is refused by one and offered by the other.
    """
    if node.layer is Layer.SPACE or is_aboard(node):
        return False
    return node.planet in await _open_planets(session)


async def _open_planets(session: AsyncSession) -> frozenset[Planet]:
    """Which planets are landed on without a port. Four rows, one reading."""

    async def read() -> frozenset[Planet]:
        spheres = (
            (
                await session.execute(
                    select(Node).where(Node.key.in_([planet.value for planet in Planet]))
                )
            )
            .scalars()
            .all()
        )
        return frozenset(
            sphere.planet for sphere in spheres if (sphere.properties or {}).get(OPEN_LANDING)
        )

    return await remember(session, ("open_landing_planets",), read)


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
    #: Only the landings whose beacon shines: a dark one is not on the console,
    #: and a route to it does not exist (D-232).
    #:
    #: And a planet one lands **anywhere** on is one line, not one per node
    #: (D-233): its fields differ in nothing the console can show -- same
    #: hours, same fuel, same class -- and their number grows with every field
    #: a scout opens. Six identical rows today, sixty later, in a socket answer
    #: sent every time the console is opened. Which node the hull comes down in
    #: is chosen on the map; until the client grows that gesture the row names
    #: one, and `ship.fly` goes on taking any surface node of the planet.
    anywhere = await _open_planets(session)
    #: And such a planet is named by its own name in the row, not by the node
    #: the row happens to carry: the hull comes down where the roll puts it
    #: (D-235), and a row promising "Плато Наковальни" would be a promise the
    #: landing does not keep.
    spheres = {
        node.planet: node.name
        for node in (
            await session.execute(
                select(Node).where(Node.key.in_([planet.value for planet in anywhere]))
            )
        )
        .scalars()
        .all()
    }
    named: set[Planet] = set()
    for port in sorted(await lit_ports(session, constants), key=lambda one: one.key):
        if docked is not None and port.id == docked.id:
            continue
        if port.planet in anywhere:
            if port.planet in named:
                continue
            named.add(port.planet)
        if port.planet not in tables:
            tables[port.planet] = await base_hours(
                session, constants, planet, port.planet, at=moment
            )
        table = tables[port.planet]
        if table is None:
            continue
        hours = passage_hours(constants, table, thrust_ratio) if thrust_ratio > 0 else None
        routes.append(
            {
                "node": port.key,
                "name": spheres.get(port.planet, port.name)
                if port.planet in anywhere
                else port.name,
                "planet": port.planet.value,
                #: What the ship is: the weakest engine aboard. Not a demand of
                #: the route -- no route makes one (D-235) -- but the number
                #: the fuel below was computed with.
                "class": have_class,
                #: The whole planet stands behind this row (D-233, D-235).
                #: There is no port to choose and no picker to draw: the node
                #: the hull comes down in is **rolled at the landing** -- one
                #: sets down where the rock allows, not where it would be
                #: convenient. The key here only names the destination planet.
                **({"anywhere": True} if port.planet in anywhere else {}),
                "hours": None if hours is None else round(hours, ROUND_HOURS),
                "fuel": (
                    None
                    if hours is None
                    else round(fuel_for(constants, weight, hours, klass=have_class), ROUND_MASS)
                ),
                #: Reachable or not is about **thrust**, and nothing else: a
                #: ship that cannot leave the ground cannot leave it for any
                #: destination. Class closes no route (D-235).
                "reachable": (
                    hours is not None
                    and have_class is not None
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
