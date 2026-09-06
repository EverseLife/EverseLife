# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""How much of the map one sees from where one stands (D-240, D-319).

The map is not the world: it is what a body sees, what its identity
remembers, and what is public. Three sources, two tones:

* **sight** -- every node of the planet within `map.sight_km` of the body, a
  distance on the globe and not a count of steps (D-319 п. 6): with honest
  metres one sees far, not along the ways. Plus the one step of the graph the
  body may actually take -- the gangway, the corridor aboard, the door -- so a
  crew aboard sees its pier and a floor sees its stair. Drawn bright;
* **memory** -- the places the identity has been to (`engine.memory`), shown
  as the world knows them now, drawn dark. A snapshot of "how it was" is not
  kept: that would be a second world beside the world (D-240's argument
  stands for snapshots);
* **the public** -- the cities of the planet and what stands inside their
  walls (D-097): a newcomer must find the door. Dark too, unless in sight.

And **the sky, always and to everybody** (D-240): a planet's place is
arithmetic over the epoch, and hiding it would hide the one thing that makes a
passage plannable. The sky carries no way in: another planet's surface is
simply not in the answer.

A node that is in none of the three is not drawn and not hidden: one may
walk to it, and on the way it comes into sight. Whatever is drawn brings its
chain of parents -- a plot needs its city to stand in (D-045, D-097).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import globe
from src.constants import Constants
from src.constants import registry as R
from src.engine import places
from src.models.world import Edge, Layer, Node
from src.units import METRES_PER_KM


@dataclass(frozen=True, slots=True)
class View:
    """What is shown, and which of it is shown dark.

    `faded` is on the wire as one flag (D-225): the client could compute the
    sight radius itself, but not whether a node outside it is remembered or
    public rather than absent -- and that is the whole difference between the
    two tones.
    """

    seen: set[uuid.UUID] = field(default_factory=set)
    faded: set[uuid.UUID] = field(default_factory=set)


def _neighbourhood(edges: Sequence[Edge]) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Who is one step from whom, over the graph as it is."""
    near: dict[uuid.UUID, set[uuid.UUID]] = {}
    for edge in edges:
        near.setdefault(edge.node_a_id, set()).add(edge.node_b_id)
        near.setdefault(edge.node_b_id, set()).add(edge.node_a_id)
    return near


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


def _standpoint(standing: Node, by_id: dict[uuid.UUID, Node]) -> globe.Geo | None:
    """Where on the globe the body stands: its node's point, or the nearest
    ancestor's -- a room aboard or a floor has no point of its own."""
    cursor: Node | None = standing
    while cursor is not None:
        point = places.geo_of(cursor)
        if point is not None:
            return point
        cursor = by_id.get(cursor.parent_id) if cursor.parent_id is not None else None
    return None


def _public(nodes: Sequence[Node], by_id: dict[uuid.UUID, Node]) -> set[uuid.UUID]:
    """The cities and what stands inside their walls (D-097): a surface node whose
    parent is itself a surface node, and that parent."""
    inside: set[uuid.UUID] = set()
    for node in nodes:
        if node.layer is not Layer.PLANET or node.parent_id is None:
            continue
        parent = by_id.get(node.parent_id)
        if parent is not None and parent.layer is Layer.PLANET:
            inside.add(node.id)
            inside.add(parent.id)
    return inside


def around(
    standing: Node | None,
    *,
    constants: Constants,
    nodes: Sequence[Node],
    edges: Sequence[Edge],
    known: Iterable[str] = (),
) -> View:
    """What this body may be shown, by id, and which of it dark.

    `standing` is where the body is, or None for whoever has no body to stand
    anywhere -- an anonymous reader, an identity in the cloud. They get the sky
    and nothing else: the surface asks for a body. `known` are the keys the
    identity remembers (`engine.memory.known`).
    """
    by_id = {node.id: node for node in nodes}
    seen = sky(nodes)
    if standing is None or standing.id not in by_id:
        return View(seen=seen)

    bright = {standing.id}
    #: The step one actually takes: a gangway, a corridor aboard, a door.
    bright |= _neighbourhood(edges).get(standing.id, set())
    #: The eye: everything of this planet's surface within the radius.
    point = _standpoint(standing, by_id)
    if point is not None:
        radius = globe.radius_m(constants, standing.planet)
        reach = float(constants[R.MAP_SIGHT_KM]) * METRES_PER_KM
        for node in nodes:
            if node.planet is not standing.planet or node.layer is not Layer.PLANET:
                continue
            where = places.geo_of(node)
            if where is not None and globe.distance_m(radius, point, where) <= reach:
                bright.add(node.id)
    bright = _with_parents(bright, by_id)

    #: Memory and the public, on this planet, dark where the eye does not reach.
    remembered = set(known)
    dark = {
        node.id
        for node in nodes
        if node.planet is standing.planet
        and node.layer is not Layer.SPACE
        and node.key in remembered
    }
    dark |= {
        node_id for node_id in _public(nodes, by_id) if by_id[node_id].planet is standing.planet
    }
    dark = _with_parents(dark, by_id) - bright
    return View(seen=seen | bright | dark, faded=dark)


async def read(session: AsyncSession) -> tuple[list[Node], list[Edge]]:
    """The whole graph, once. The filtering is arithmetic over it.

    **This is the map's remaining cost, and it is named rather than hidden.**
    Two tables that grow with every plot, vein, hull and compartment are read
    per request, and the walk over them is one pass per node and per edge.
    The cure, when the world is big enough to need one, is a bounding-box
    query over the planet's surface (the same one exploration's aim wants) and
    the identity's memory joined to it -- not done here because a wrong
    neighbourhood is a player seeing what they must not, and the plain
    version is the one that can be read and believed.
    """
    nodes = list((await session.execute(select(Node))).scalars().all())
    edges = list((await session.execute(select(Edge))).scalars().all())
    return nodes, edges
