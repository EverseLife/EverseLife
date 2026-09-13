# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What is seen from where one stands: the scene from the pier and from
aboard, with the hull's air in the corner of the eye.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.engine import places, travel
from src.engine.ship.belonging import is_aboard, nodes_of, of_node
from src.models.ship import Ship
from src.models.world import Edge, Node


def _oxygen():
    """The oxygen module, imported late.

    `oxygen` reads a hull through this very package, so the import cannot stand
    at the top: this is the one edge of that cycle, named where it is broken.
    """
    from src.engine import oxygen  # noqa: PLC0415 -- lazy: breaks the cycle with oxygen

    return oxygen


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


def berthed_place(constants: Constants, port: Node, ship: Ship) -> dict[str, float] | None:
    """Where a moored hull stands on the map: beside its pier, a gap out.

    A hull has no place of its own on the surface. Its own node is the
    interior's -- flat `x, y` in the ship's own window (`places`) -- and the
    pier level had no point for it at all, so the map drew nothing: the
    gangway led to a node that was nowhere, one could not walk aboard, and
    from aboard one could not find oneself. The pier lends the hull a point
    instead (`places.beside`): a gap from the port's own mark, in a direction
    the hull's own id decides, so that two hulls at one pier do not coincide
    -- not even on a world one lands anywhere on, where every one of them is
    berth one (D-233).

    Not a place in the sense of D-237 -- nothing is written, nothing is
    reserved, and a hull that casts off takes it with it. None where the pier
    is not ground at all: a hull "moored" to an orbital node hangs in the sky,
    and the sky draws it from the clock (D-289).
    """
    spot = places.beside(constants, port, str(ship.id))
    return None if spot is None else places.wire_geo(spot)


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
                #: The pier's own point, one berth out (`berthed_place`). The
                #: client draws by the place and by nothing else since D-319:
                #: without this the hull was on the wire and not on the map,
                #: and the gangway in `exits` had nothing to click.
                "place": berthed_place(constants, port, ship),
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
                    "seconds": round(travel.edge_seconds(constants, gangway, snow=0.0)),
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
    city = None
    if port is not None:
        keys[port.id] = port.key
        #: The city the pier stands in, so the hull hangs under it the way the
        #: pier does -- that is what makes it a point of the city's scene.
        if port.parent_id is not None:
            city = await session.get(Node, port.parent_id)

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
                #: Where the room stands on the ship's **own** map (D-237,
                #: D-240). Sent only from aboard: from the pier a hull is one
                #: node, and these coordinates are the interior's -- drawing a
                #: moored ship by them would put its cabins across the city.
                #: With this the client stops settling a hull with springs, and
                #: the arrangement its owner made is the one everybody aboard
                #: sees.
                "place": places.wire(room),
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
        ]
        + _moored_hull(constants, ship, delegate, port, city),
        "edges": [
            {
                "a": keys[edge.node_a_id],
                "b": keys[edge.node_b_id],
                "surface": edge.surface.value,
                "seconds": round(travel.edge_seconds(constants, edge, snow=0.0)),
            }
            for edge in ways
            if edge.node_a_id in keys and edge.node_b_id in keys
        ],
        #: Whether the hull is off its pier -- under way or adrift -- as
        #: against moored at a pier or on its parking circle. The rooms carry
        #: no pier and no orbit, and the hull itself is not in this answer
        #: once it has cast off, so nothing else here could say it (D-225).
        #: The music aboard is what asks (D-333).
        "underway": ship.docked_node_id is None and ship.lost_at is None,
    }


def _moored_hull(
    constants: Constants,
    ship: Ship,
    delegate: Node | None,
    port: Node | None,
    city: Node | None,
) -> list[dict[str, object]]:
    """The hull itself, on the pier's level, for the crew standing in it.

    A room aboard stands on the ship's own flat map and hangs under the ship's
    delegate, which hangs under the planet -- so on the surface a crew stood in
    a node with no place and under no city, and the map could not draw them at
    all: no "you are here", nothing for the camera to follow, and the frame
    opened over whatever city came first. This is the hull as the pier sees it
    (`_from_pier`), sent to those aboard as well, so that the delegate their
    rooms already hang under is a point of the surface with the port beside it.

    Only while moored: a hull in flight or in orbit is the sky's, and the sky
    draws it from the clock (D-289).
    """
    place = None if port is None else berthed_place(constants, port, ship)
    if delegate is None or port is None or place is None:
        return []
    return [
        {
            "key": delegate.key,
            "name": ship.name,
            "layer": port.layer.value,
            "parent": None if city is None else city.key,
            "place": place,
            "exit": False,
            "port": False,
            "planet": delegate.planet.value,
            "orbit": None,
            "deferred": False,
            "aboard": True,
            "flight": None,
        }
    ]
