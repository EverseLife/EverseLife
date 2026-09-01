# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The map's edges: what leads out of a place, who the neighbours are, and
how a way is laid or taken up -- never from under somebody walking it.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.engine import city as town
from src.engine import (
    estate,
    net,
)
from src.engine import ship as vessels
from src.engine.travel._base import EdgeInUse, Exit, NotAnExit, _edge_between, edge_seconds, is_exit
from src.models.travel import Travel, TravelState
from src.models.world import Edge, Node, Surface


async def exits(session: AsyncSession, constants: Constants, node: Node) -> tuple[Exit, ...]:
    """Where the edges from the node lead. An edge is undirected, so we look at both ends."""
    rows = (
        (
            await session.execute(
                select(Edge).where(or_(Edge.node_a_id == node.id, Edge.node_b_id == node.id))
            )
        )
        .scalars()
        .all()
    )
    #: All the far ends at once. One by one this was a query per road, and the
    #: node scene asks for exits on every look -- the commonest command there is.
    far = {edge.node_b_id if edge.node_a_id == node.id else edge.node_a_id for edge in rows}
    beyond = (
        {
            other.id: other
            for other in (await session.execute(select(Node).where(Node.id.in_(far)))).scalars()
        }
        if far
        else {}
    )

    found: list[Exit] = []
    for edge in rows:
        other_id = edge.node_b_id if edge.node_a_id == node.id else edge.node_a_id
        other = beyond.get(other_id)
        if other is None:  # pragma: no cover -- an edge to nowhere is a bug
            continue
        found.append(
            Exit(
                edge_id=edge.id,
                node_id=other.id,
                key=other.key,
                name=other.name,
                surface=edge.surface,
                seconds=edge_seconds(constants, edge),
                condition=float(edge.condition),
            )
        )
    return tuple(sorted(found, key=lambda exit: exit.seconds))


async def connect(
    session: AsyncSession,
    a: Node,
    b: Node,
    *,
    base_seconds: float,
    surface: Surface = Surface.ROAD,
) -> Edge:
    """Connect two nodes with an edge. Undirected -- the road is the same both ways.

    The docking half of the pair (D-201): a ship couples to a spaceport by one
    edge between its connector and the port node, and nothing else in the graph
    changes. Idempotent -- an existing edge is returned rather than doubled, so
    a repeated docking does not give a second way in.

    An edge is created nowhere else in the engine, so this is also the one place
    the city's boundary can be held: across it only the gate and the spaceport
    are connected (D-206).
    """
    existing = await _edge_between(session, a.id, b.id)
    if existing is not None:
        return existing
    await require_exit(session, a, b)
    #: Asked before the edge exists: whether either end is a place nothing led
    #: to yet. See below -- that decides whether measured distances survive.

    dead_end = await _unconnected(session, a) or await _unconnected(session, b)
    afloat = vessels.is_aboard(a) or vessels.is_aboard(b)
    edge = Edge(
        node_a_id=a.id,
        node_b_id=b.id,
        base_seconds=int(base_seconds),
        surface=surface,
    )
    session.add(edge)
    await session.flush()
    #: Land is priced by the distance to the city's printer (D-220), and this
    #: is the one place an edge appears -- so it is the one place a measured
    #: distance can go stale. It does not always go stale, and the difference
    #: is worth keeping:
    #:
    #: * **a way to a new place changes nothing.** A scout's trail hangs a
    #:   node nothing led to yet, and a road through it would have to come back
    #:   along the same edge -- so it lies on nobody's shortest way. This is the
    #:   common case: the map grows at its edges;
    #: * **a gangway changes nothing either.** A ship hangs on the map by that
    #:   one edge (D-201), so a road through it comes back the way it went; and
    #:   no ship node belongs to a city, so no land tax is measured for one at
    #:   all. Docking used to drop the whole world's measurements for nothing;
    #: * **a way between two places already on the map may be a short cut**, and
    #:   then a whole quarter is nearer the centre than it was measured to be.
    #:   Which quarter is not asked: the measurements are dropped and taken
    #:   again by whoever needs one. Nobody lays such an edge in play -- roads
    #:   only re-surface edges that exist -- so in practice this is the seed
    #:   catching an already-living world up to a changed map, once per deploy.

    if dead_end and not afloat:
        #: The map grew at its edge: the new place stands one step further from
        #: the printer than what it was hung on, and that is the whole of
        #: measuring in play (D-220). Nothing else moved, so nothing else is
        #: touched.
        await estate.note_new_place(session, a, b)
    elif not afloat:
        await estate.forget_distances(session)
    #: The Net's map of the roads changed either way, gangway or not (D-222).

    net.forget_graph()
    return edge


async def _unconnected(session: AsyncSession, node: Node) -> bool:
    """Whether no edge leads to this node at all."""
    found = await session.scalar(
        select(Edge.id).where(or_(Edge.node_a_id == node.id, Edge.node_b_id == node.id)).limit(1)
    )
    return found is None


async def require_exit(session: AsyncSession, a: Node, b: Node) -> None:
    """An edge across a city's boundary is allowed only at its doors (D-206).

    Two ends, and each is checked on its own: a road between two cities leaves
    one gate and arrives at another. What is not checked is an edge inside a
    single city -- a street is not a border -- and an edge between nodes outside
    every city: wild land has no walls, so it has no doors either.
    """

    here = await town.of_node(session, a)
    there = await town.of_node(session, b)
    if here is not None and there is not None and here.id == there.id:
        return
    for node, city in ((a, here), (b, there)):
        if city is None or await is_exit(session, node):
            continue
        raise NotAnExit(key="travel-not-an-exit", node=node.name)


async def disconnect(session: AsyncSession, a: Node, b: Node) -> bool:
    """Remove the edge between two nodes -- undocking (D-201).

    The edge is **removed**, not flagged as closed: a second state would have
    to be accounted for in routing, in exploration and in the node scene, and a
    ship in flight is unreachable for exactly the reason any disconnected piece
    of the map is -- there is no path to it.

    The single precondition: **nobody is walking this edge**. A transit under
    way would hang between a node that is no longer adjacent and a body with
    nowhere to arrive, so undocking waits for the gangway to clear. Routes laid
    through the edge are another matter: they break off at the node the body
    reached, like any other route that ran into the impassable.

    Returns whether there was anything to remove: undocking an undocked ship is
    not an error, it is a no-op.
    """
    edge = await _edge_between(session, a.id, b.id)
    if edge is None:
        return False

    walking = (
        (
            await session.execute(
                select(Travel).where(Travel.edge_id == edge.id, Travel.state == TravelState.GOING)
            )
        )
        .scalars()
        .first()
    )
    if walking is not None:
        raise EdgeInUse(key="travel-edge-in-use")

    await session.delete(edge)
    await session.flush()
    #: A way that is gone may make the road to the centre longer than it was
    #: measured to be, and land is priced by that distance (D-220) -- the same
    #: reason as in `connect`, and the same exception: a ship hung on the map
    #: by this one gangway (D-201), and nothing on land counted its way through
    #: a hull. Today that is every removal there is; the check is here for the
    #: day something on land is taken apart.

    if not (vessels.is_aboard(a) or vessels.is_aboard(b)):
        await estate.forget_distances(session)

    net.forget_graph()
    return True


async def neighbours(session: AsyncSession, node: Node) -> Sequence[Node]:  # pragma: no cover
    """The node's neighbours -- for the map and the future autopath (D-045)."""
    edges = (
        (
            await session.execute(
                select(Edge).where(or_(Edge.node_a_id == node.id, Edge.node_b_id == node.id))
            )
        )
        .scalars()
        .all()
    )
    out: list[Node] = []
    for edge in edges:
        other_id = edge.node_b_id if edge.node_a_id == node.id else edge.node_a_id
        other = await session.get(Node, other_id)
        if other is not None:
            out.append(other)
    return out
