# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""How much of the map one sees from where one stands (D-240, D-319).

The map is not the world: it is what a body sees, what its identity
remembers, and what is public. Three sources, two tones:

* **sight** -- every node of the planet within `map.sight_km` of the body
  **that the ground does not hide** (`engine.horizon`, landscape plan wave 9),
  a distance on the globe and not a count of steps (D-319 п. 6): with honest
  metres one sees far, not along the ways. The land may take from the radius
  and never adds to it (owner, 2026-09-09), so the plain is what it was and
  in broken country a neighbour behind a rise is not there until one walks
  to it. Plus the one step of the graph the body may actually take -- the
  gangway, the corridor aboard, the door -- so a crew aboard sees its pier
  and a floor sees its stair. Drawn bright;
* **memory** -- the places the identity has been to (`engine.memory`), shown
  as the world knows them now, drawn dark. A snapshot of "how it was" is not
  kept: that would be a second world beside the world (D-240's argument
  stands for snapshots);
* **the public** -- the cities of the planet and what stands inside their
  walls (D-097): a newcomer must find the door. Dark too, unless in sight.
  A city is a polity's node (`City`), not any node with children: a frozen
  city of the Forerunners is a find, and a find is known by sight and memory.

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
from src.engine import horizon, places
from src.models.world import Edge, Layer, Node, Surface


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
    #: The public part of the dark: cities and highways, known to all. Not
    #: on the wire -- the map needs it to tell what is known from a map alone.
    public: set[uuid.UUID] = field(default_factory=set)


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


def _public(nodes: Sequence[Node], edges: Sequence[Edge], cities: set[uuid.UUID]) -> set[uuid.UUID]:
    """The cities, what stands inside their walls, and the highways (D-097):
    the polities' nodes, every surface node hanging on one of them, and every
    surface node a laid road or a paved way touches -- a road is work (D-107),
    and work in the open is seen from afar."""
    by_id = {node.id: node for node in nodes}
    inside = set(cities)
    for node in nodes:
        if node.layer is Layer.PLANET and node.parent_id in cities:
            inside.add(node.id)
    for edge in edges:
        if edge.surface in (Surface.ROAD, Surface.PAVED):
            for end in (edge.node_a_id, edge.node_b_id):
                node = by_id.get(end)
                if node is not None and node.layer is Layer.PLANET:
                    inside.add(end)
    return inside


def around(
    standing: Node | None,
    *,
    constants: Constants,
    nodes: Sequence[Node],
    edges: Sequence[Edge],
    known: Iterable[str] = (),
    cities: Iterable[uuid.UUID] = (),
) -> View:
    """What this body may be shown, by id, and which of it dark.

    `standing` is where the body is, or None for whoever has no body to stand
    anywhere -- an anonymous reader, an identity in the cloud. They get the sky
    and nothing else: the surface asks for a body. `known` are the keys the
    identity remembers (`engine.memory.known`); `cities` the nodes of the
    polities (`City.node_id`), which are public.
    """
    by_id = {node.id: node for node in nodes}
    seen = sky(nodes)
    if standing is None or standing.id not in by_id:
        return View(seen=seen)

    bright = {standing.id}
    #: The step into or out of an inside: a gangway, a corridor aboard, a
    #: door. Only where a point of the globe is missing on one side -- a
    #: room, a hull -- because the eye is a radius (D-319 п. 6), and a road
    #: neighbour fifteen kilometres off is not in sight for being joined.
    for other_id in _neighbourhood(edges).get(standing.id, set()):
        other = by_id.get(other_id)
        if other is None:
            continue
        if places.geo_of(standing) is None or places.geo_of(other) is None:
            bright.add(other_id)
    #: The eye: everything of this planet's surface within the radius that
    #: the ground does not hide. The radius is asked first because it is one
    #: subtraction against a walk over the land between (`horizon.hidden`),
    #: and on a planet of thousands of nodes all but a handful fail it.
    point = _standpoint(standing, by_id)
    if point is not None:
        radius = globe.radius_m(constants, standing.planet)
        #: How far the eye reaches from this very place: the vault's radius,
        #: and never further than the planet's own curve allows from the
        #: height one stands at. One rule rather than two -- on this planet
        #: the radius is what binds everywhere on land, and it stays the
        #: rule if the owner ever raises it.
        reach = horizon.reach_m(constants, standing.planet, point)
        for node in nodes:
            if node.planet is not standing.planet or node.layer is not Layer.PLANET:
                continue
            where = places.geo_of(node)
            if where is None:
                continue
            span = globe.distance_m(radius, point, where)
            if span > reach:
                continue
            if horizon.hidden(constants, standing.planet, point, where, radius=radius, span=span):
                continue
            bright.add(node.id)
    bright = _with_parents(bright, by_id)

    #: Memory and the public, on this planet, dark where the eye does not reach.
    remembered = set(known)
    #: Memory is of the surface: a floor or a cabin is the inside window's
    #: (D-319 п. 9), and remembering it would drag its house or hull onto the
    #: map dark from a memory of a room.
    dark = {
        node.id
        for node in nodes
        if node.planet is standing.planet and node.layer is Layer.PLANET and node.key in remembered
    }
    public = {
        node_id
        for node_id in _public(nodes, edges, set(cities))
        if node_id in by_id and by_id[node_id].planet is standing.planet
    }
    dark = _with_parents(dark | public, by_id) - bright
    return View(seen=seen | bright | dark, faded=dark, public=public - bright)


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
