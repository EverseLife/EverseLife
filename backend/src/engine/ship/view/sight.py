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
