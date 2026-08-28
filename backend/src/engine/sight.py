# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""How much of the map one sees from where one stands (D-240).

Until now the map was the world: `/public/map` answered with every node and
every edge there was, to anybody, with no token. That was a decision -- cities
and highways are public so that a newcomer finds where to go (D-097) -- and it
outlived its reason twice over. It made **exploration worth nothing**: a planet
opened by somebody else's scout was on everybody's screen the same second, and
a find was news rather than knowledge. And it opened planets nobody could reach:
one clicked Aurora's sphere and read its cities without owning a ship.

So the map is a **neighbourhood**, not a world:

* **two steps of the graph** from the node the body stands in. Not a distance
  and not a radius: steps, the same units everything else about movement is in
  (D-045). Two, because one shows the ways out with nothing to choose between,
  and three already draws the next city;
* **one step on the planet's surface**, where a step is a whole group -- a city,
  a camp, a field. Past that lies what one still has to walk to;
* **the sky, always and to everybody**. A planet's place is a function of the
  epoch and its own orbit (D-237): it is arithmetic, not intelligence, and
  hiding it would hide the one thing that makes a passage plannable. What the
  sky does **not** carry is any way in: the surfaces of other planets are simply
  not in the answer, so there is nothing to expand.

What was found is **not remembered**: the fog closes behind the walker. Keeping
a personal map of everything ever seen would be a second world beside the world,
growing per player and never shrinking -- and the thing that makes a place worth
remembering is that one has to remember it. That is the same reason node places
are fixed for ever (D-237): a map worth learning has to hold still.

## Ancestors travel with everything

A node is drawn on the layer of its group, and the client climbs the `parent`
chain to find it (D-045, D-097). So whatever is visible brings its whole chain
of parents with it -- otherwise a plot would arrive with no city to stand in and
the layer above would come out empty.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.world import Edge, Layer, Node
from src.runtime import MAP_SIGHT, MAP_SIGHT_PLANET


def _step_out(
    start: Iterable[uuid.UUID], near: dict[uuid.UUID, set[uuid.UUID]], depth: int
) -> set[uuid.UUID]:
    """Breadth-first out of `start`, `depth` steps and no further."""
    seen = set(start)
    edge = list(seen)
    for _ in range(depth):
        if not edge:
            break
        further: list[uuid.UUID] = []
        for here in edge:
            for other in near.get(here, ()):
                if other in seen:
                    continue
                seen.add(other)
                further.append(other)
        edge = further
    return seen


def _neighbourhood(edges: Sequence[Edge]) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Who is one step from whom, over the graph as it is."""
    near: dict[uuid.UUID, set[uuid.UUID]] = {}
    for edge in edges:
        near.setdefault(edge.node_a_id, set()).add(edge.node_b_id)
        near.setdefault(edge.node_b_id, set()).add(edge.node_a_id)
    return near


def _delegate(node_id: uuid.UUID, layer: Layer, by_id: dict[uuid.UUID, Node]) -> uuid.UUID | None:
    """The node standing for this one on that layer -- itself or an ancestor."""
    cursor = by_id.get(node_id)
    while cursor is not None:
        if cursor.layer is layer:
            return cursor.id
        cursor = None if cursor.parent_id is None else by_id.get(cursor.parent_id)
    return None


def _with_parents(ids: set[uuid.UUID], by_id: dict[uuid.UUID, Node]) -> set[uuid.UUID]:
    """Everything named, plus every ancestor of it: a node needs its group."""
    whole = set(ids)
    for node_id in ids:
        cursor = by_id.get(node_id)
        while cursor is not None and cursor.parent_id is not None:
            if cursor.parent_id in whole:
                break
            whole.add(cursor.parent_id)
            cursor = by_id.get(cursor.parent_id)
    return whole


def sky(nodes: Iterable[Node]) -> set[uuid.UUID]:
    """The space layer: the planets, and the hulls standing in it.

    Everybody's, always. A planet's place comes from the epoch and its orbit,
    and a hull under way is drawn from the passage that carries it -- neither is
    anybody's secret, and both are what makes a passage plannable at all.
    """
    return {node.id for node in nodes if node.layer is Layer.SPACE}


def around(
    standing: Node | None, *, nodes: Sequence[Node], edges: Sequence[Edge]
) -> set[uuid.UUID]:
    """Which nodes this body may be shown, by id.

    `standing` is where the body is, or None for whoever has no body to stand
    anywhere -- an anonymous reader, an identity in the cloud. They get the sky
    and nothing else: the surface asks for a body.
    """
    by_id = {node.id: node for node in nodes}
    seen = sky(nodes)
    if standing is None or standing.id not in by_id:
        return seen

    #: The walk one actually walks: two steps over the graph as it is, so a
    #: gangway, a corridor aboard and a road out of the gate all count as the
    #: one step each of them is.
    near = _neighbourhood(edges)
    seen |= _step_out([standing.id], near, MAP_SIGHT)

    #: And one step of the planet's own map, where a step is a whole group.
    #: Projected rather than walked: on that layer a road from a gate to a field
    #: joins the **city** and the field (D-045, D-206), and it is that joining
    #: the surface is drawn by.
    surface = _delegate(standing.id, Layer.PLANET, by_id)
    if surface is not None:
        projected: dict[uuid.UUID, set[uuid.UUID]] = {}
        for edge in edges:
            one = _delegate(edge.node_a_id, Layer.PLANET, by_id)
            other = _delegate(edge.node_b_id, Layer.PLANET, by_id)
            if one is None or other is None or one == other:
                continue
            projected.setdefault(one, set()).add(other)
            projected.setdefault(other, set()).add(one)
        seen |= _step_out([surface], projected, MAP_SIGHT_PLANET)

    return _with_parents(seen, by_id)


async def read(session: AsyncSession) -> tuple[list[Node], list[Edge]]:
    """The whole graph, once. The filtering is arithmetic over it.

    **This is the map's remaining cost, and it is named rather than hidden.**
    The route read every node and every edge before this rule too, so nothing
    got slower by it; what changed is that the answer is now personal, so it
    can no longer be computed once for everybody. Two tables that grow with
    every plot, vein, hull and compartment are read per request, and the walk
    over them is one pass per edge.

    The cure, when the world is big enough to need one, is to unwind the
    neighbourhood with queries from the body's node instead of in Python -- a
    recursive CTE over `edge`, two levels deep. It is not done here because a
    wrong neighbourhood is a player seeing what they must not, and the plain
    version is the one that can be read and believed.
    """
    nodes = list((await session.execute(select(Node))).scalars().all())
    edges = list((await session.execute(select(Edge))).scalars().all())
    return nodes, edges
