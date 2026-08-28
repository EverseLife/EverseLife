# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Where a node stands on the map, once and for everybody (D-237).

Until now the map had no places at all. The client settled the graph with
springs every time it was opened, and a spring layout has no preferred
orientation: the same three nodes came out turned differently on two openings,
turned differently again for the neighbour looking at the same city, and turned
differently once more after a find. Nobody could say "the mine is north of the
gate", because there was no north and no gate to be north of.

So a place is a property of the node, like its area:

* **assigned once**, when the node is created, and never recomputed. The world
  is eternal and has no wipes (D-007) -- a map that redrew itself would be the
  one thing in it that does;
* **next to the node it was laid from**. The anchor is passed by whoever
  creates the node -- exploration knows which node the scout left from, a room
  knows the corridor it opened off, a ship's node knows the one it was laid
  from -- so an edge on the map is short and the graph reads as a graph;
* **in the coordinates of its own layer**. A find beyond the walls stands next
  to the *city* on the planet's map (D-206), because that is what the whole
  city is on that map; a plot inside stands next to the very node it was sought
  from.

The client draws what it is given and computes nothing. That is the whole
point: one map for every player, the same one tomorrow, and no rotation.

## What is not placed here

The space layer. A planet's place is a function of time -- angle from the
world's epoch, radius from the vault (`world.orbit_of`) -- and a stored point
would be a second, lying opinion about where it is. A ship stands beside its
planet, which is the same rule.

## Units

Map units, not metres and not pixels: `runtime.MAP_STEP` and its neighbours are
execution numbers (D-065 does not apply -- a node standing a step further from
another changes nothing in the world; what costs time is the edge's seconds).
The client fits them into whatever frame it has.
"""

from __future__ import annotations

import hashlib
import math

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine.errors import Refusal
from src.models.world import Edge, Layer, Node
from src.runtime import (
    MAP_HASH_SPAN,
    MAP_HASH_STEP,
    MAP_LOCK_BYTES,
    MAP_MIN_GAP,
    MAP_RINGS,
    MAP_STEP,
    MAP_TURN,
)

#: The node property the place lives in. A data key of the world, like
#: "кольцо" and "орбита" beside it -- and a `properties` key rather than a
#: column, because a place is a fact about one node and needs no index.
PLACE = "карта"
PLACE_X = "x"
PLACE_Y = "y"

#: Where a group's first node stands when there is nothing to stand next to.
ORIGIN = (0.0, 0.0)


class PlaceIsFixed(Refusal):
    """A node of the world does not move: its place is given once (D-237)."""


#: The layers a place is stored for. Space is not among them: there a node's
#: point is computed from the clock, and a stored one would contradict it.
PLACED = (Layer.PLANET, Layer.CITY, Layer.LOCATION)


def _point(properties: dict | None) -> tuple[float, float] | None:
    """The place out of a node's properties, whether the row came whole or by columns."""
    point = (properties or {}).get(PLACE)
    if not isinstance(point, dict):
        return None
    try:
        return float(point[PLACE_X]), float(point[PLACE_Y])
    except (KeyError, TypeError, ValueError):  # pragma: no cover -- a hand-edited row
        return None


def place_of(node: Node) -> tuple[float, float] | None:
    """The node's place, or None if it has none (space, or a node from before D-237)."""
    return _point(node.properties)


def wire(node: Node) -> dict[str, float] | None:
    """The place as the client is told it. The data key is the world's, this one is the code's."""
    point = place_of(node)
    return None if point is None else {"x": point[0], "y": point[1]}


def _direction(key: str) -> float:
    """Which way this node leans off its anchor -- its own, and always the same.

    Read off the key rather than rolled: two servers replaying the same world
    must lay the same map, and a node keeps its key for ever (D-007).
    """
    seed = 0
    for letter in key:
        seed = (seed * MAP_HASH_STEP + ord(letter)) % MAP_HASH_SPAN
    return (seed / MAP_HASH_SPAN) * math.tau


async def _neighbourhood(session: AsyncSession, node: Node) -> list[tuple[float, float]]:
    """The places already taken on this node's own map.

    A map is per group, not per world: the rooms of one house do not crowd the
    rooms of another, and two planets share no ground. So the built-up layer
    and the sub-nodes compete inside their parent, and the planet's surface
    inside its planet.

    Two columns, not whole rows: a planet's surface grows with every find and
    has no ceiling, and this runs on every node created.
    """
    if node.layer is Layer.PLANET:
        where = Node.planet == node.planet
    else:
        where = Node.parent_id == node.parent_id
    rows = await session.execute(
        select(Node.id, Node.properties).where(Node.layer == node.layer, where)
    )
    taken = []
    for other_id, properties in rows:
        if other_id == node.id:
            continue
        point = _point(properties)
        if point is not None:
            taken.append(point)
    return taken


async def _hold(session: AsyncSession, node: Node) -> None:
    """Hold this node's map until the transaction ends.

    Two scouts returning in the same second, two rooms opened at once: both read
    the same taken places and both pick the same free spot. A place is never
    recomputed, so an overlap made here would be permanent and there would be
    nothing left to fix it with. The lock is per group -- one city, one planet's
    surface -- so laying out Terra does not wait on laying out Aurora.
    """
    stamp = hashlib.blake2b(str(_group(node)).encode(), digest_size=MAP_LOCK_BYTES).digest()
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int.from_bytes(stamp, "big", signed=True)},
    )


async def _centre(session: AsyncSession, node: Node, anchor: Node | None) -> tuple[float, float]:
    """What the new node is laid next to, in its own layer's coordinates.

    The anchor may stand on a lower layer than the node being placed -- a find
    beyond the walls is sought from a node inside a city, and on the planet's
    map that whole city is one point (D-206). So we climb to the delegate of
    the anchor on this node's layer, which is the point the map actually draws.
    """
    cursor = anchor
    while cursor is not None:
        if cursor.layer is node.layer:
            return place_of(cursor) or ORIGIN
        if cursor.parent_id is None:
            break
        cursor = await session.get(Node, cursor.parent_id)
    return ORIGIN


def _free(spot: tuple[float, float], taken: list[tuple[float, float]]) -> bool:
    return all(math.hypot(spot[0] - x, spot[1] - y) >= MAP_MIN_GAP for x, y in taken)


def _seat(
    centre: tuple[float, float], taken: list[tuple[float, float]], lean: float
) -> tuple[float, float]:
    """A free place on some ring round the centre. Never one already taken.

    **How many seats a ring has is its own circumference's business.** Twelve on
    every ring put the near rings' seats on top of each other and the far rings'
    a screen apart, and the search gave up after `MAP_RINGS` of them -- after
    which the old code kept whatever spot it had computed last, occupied or not.
    A place is never recomputed (D-007), so that overlap was for ever, and on a
    planet's surface it was not an exotic case: every find of one city anchors
    to the same point, so a mature planet reached the ceiling by ordinary play.

    Widening always ends: a ring's room grows with its radius while the places
    already taken are finite. The walk out is bounded all the same, and past the
    bound the node is seated a whole step beyond everything on the map, which is
    further from every taken place than the gap and so cannot fail the test.
    """
    #: A ring per node already placed, and `MAP_RINGS` on top: the innermost
    #: ring alone seats several, so the walk cannot reach this before it finds
    #: room. It is a guard against a misconfigured gap, not a policy.
    rings = len(taken) + MAP_RINGS
    for ring in range(1, rings + 1):
        radius = MAP_STEP * ring
        #: The golden angle keeps the tried directions off rays whatever the
        #: count; how many are worth trying is what the ring can actually hold.
        for seat in range(max(1, int(math.tau * radius / MAP_MIN_GAP))):
            angle = lean + MAP_TURN * seat
            spot = (centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle))
            if _free(spot, taken):
                return spot

    #: Unreachable while the gap is smaller than a ring's circumference, and
    #: still not a place to guess from: put the node past the whole map rather
    #: than on somebody's head. A step is wider than a gap, so the distance from
    #: here to the furthest taken place already clears the test.
    edge = max(  # pragma: no cover -- a gap wider than the rings it is measured on
        (math.hypot(x - centre[0], y - centre[1]) for x, y in taken), default=0.0
    )
    far = edge + MAP_STEP  # pragma: no cover
    return (centre[0] + far * math.cos(lean), centre[1] + far * math.sin(lean))  # pragma: no cover


async def assign(
    session: AsyncSession,
    node: Node,
    *,
    anchor: Node | None = None,
    taken: list[tuple[float, float]] | None = None,
) -> None:
    """Give the node its place, next to the anchor and clear of everybody else.

    Called once, from `world.create_node`. A node that already has a place
    keeps it: this is the one property of a node that never moves.

    `taken` is the group's occupied places when the caller already holds them --
    the backfill walks a whole group and would otherwise read the same rows back
    once per node. Left out, they are read here, under the group's lock.
    """
    if node.layer not in PLACED or place_of(node) is not None:
        return
    if taken is None:
        await _hold(session, node)
        taken = await _neighbourhood(session, node)
    centre = await _centre(session, node, anchor)
    #: The group's own first node stands at its origin: there is nothing to
    #: stand beside, and a first node pushed onto a ring would make a forgotten
    #: anchor and a lone node look exactly alike.
    spot = centre if _free(centre, taken) else _seat(centre, taken, _direction(node.key))
    taken.append(spot)
    node.properties = {**(node.properties or {}), PLACE: {PLACE_X: spot[0], PLACE_Y: spot[1]}}
    await session.flush()


async def move(session: AsyncSession, node: Node, spot: tuple[float, float]) -> None:
    """Put an existing node at this place -- the one way a place ever changes.

    **Ground never moves** (D-237): the capital stands where it stands, for
    everybody and tomorrow, and that is the whole worth of the rule. The single
    exception is a room aboard a ship (D-240), and it is an exception for a
    reason of the same kind: a ship's interior is not on the public map at all
    (D-201). Nobody else sees it, so there is no shared north to break and no
    neighbour to disagree with -- there is only the owner, arranging their own
    rooms into a shape they can read.

    Refused for anything else here rather than at the caller: one rule, one
    place, and no second way to write a place into a node.
    """
    #: Lazy: `ship` reaches `places` through `world.create_node`, and the mark
    #: it reads is one key of `properties` -- the cycle is not worth a column.
    from src.engine.ship import is_aboard  # noqa: PLC0415 -- lazy: breaks the cycle with ship

    if not is_aboard(node):
        raise PlaceIsFixed(
            f"«{node.name}» стоит на карте мира: место узла задаётся один раз "
            "и не двигается. Переставлять можно только отсеки корабля"
        )
    await _hold(session, node)
    node.properties = {**(node.properties or {}), PLACE: {PLACE_X: spot[0], PLACE_Y: spot[1]}}
    await session.flush()


def _group(node: Node) -> tuple[object, ...]:
    """Which map this node is drawn on: one planet's surface, one city, one house."""
    if node.layer is Layer.PLANET:
        return (node.layer, node.planet)
    return (node.layer, node.parent_id)


def _delegate(node: Node, layer: Layer, by_id: dict[object, Node]) -> Node | None:
    """The node that stands for this one on that layer -- itself or an ancestor."""
    cursor: Node | None = node
    while cursor is not None:
        if cursor.layer is layer:
            return cursor
        cursor = by_id.get(cursor.parent_id) if cursor.parent_id is not None else None
    return None


async def backfill(session: AsyncSession) -> int:
    """Give a place to every node laid before D-237. Returns how many were placed.

    The world is eternal and has no wipes (D-007), so "recreate the database"
    is not an answer here either: the capital, its plots and everything already
    explored have to get their places without moving anybody who has one. Runs
    with the catching-up seed at every deploy, and does nothing at all the
    second time -- a node with a place keeps it.

    The order is what makes the result readable: the group's own first node
    takes the origin, and from there the walk goes outwards along the edges, so
    every node is placed next to a neighbour that already has a place --
    exactly as `assign` would have done at creation. What no edge reaches goes
    round that first node.
    """
    #: Asked before the world is read, because the answer is almost always "no
    #: one": this runs at every deploy, and from the second deploy on it walks
    #: every node and every edge of a growing world to place nobody.
    if not await session.scalar(
        select(Node.id).where(Node.layer.in_(PLACED), ~Node.properties.has_key(PLACE)).limit(1)
    ):
        return 0

    nodes = list((await session.execute(select(Node))).scalars().all())
    by_id: dict[object, Node] = {node.id: node for node in nodes}
    edges = list((await session.execute(select(Edge))).scalars().all())

    groups: dict[tuple[object, ...], list[Node]] = {}
    for node in nodes:
        if node.layer in PLACED:
            groups.setdefault(_group(node), []).append(node)

    #: Edges projected onto the layer they are looked at from: a road from a
    #: city gate to a wild field joins, on the planet's map, the city and the
    #: field (D-045).
    links: dict[tuple[object, ...], dict[object, set[object]]] = {}
    for edge in edges:
        one, other = by_id.get(edge.node_a_id), by_id.get(edge.node_b_id)
        if one is None or other is None:  # pragma: no cover -- edges follow their nodes
            continue
        for layer in PLACED:
            here, there = _delegate(one, layer, by_id), _delegate(other, layer, by_id)
            if here is None or there is None or here.id == there.id:
                continue
            if _group(here) != _group(there):  # pragma: no cover -- two planets share no edge
                continue
            near = links.setdefault(_group(here), {})
            near.setdefault(here.id, set()).add(there.id)
            near.setdefault(there.id, set()).add(here.id)

    placed = 0
    for group, members in sorted(groups.items(), key=lambda pair: str(pair[0])):
        #: The group's own first node is its centre, and it is the same one on
        #: every server: age first, key to break a tie.
        order = sorted(members, key=lambda node: (node.created_at, node.key))
        root = order[0]
        #: The whole group's occupied places, carried through the walk: `assign`
        #: appends to this list, so nothing is read back per node.
        taken = [point for point in map(place_of, members) if point is not None]
        #: **The root goes first**, and it goes to the origin. Walking out from a
        #: node that has no place yet would seat the entire first ring around
        #: nought instead of around the root.
        if place_of(root) is None:
            await assign(session, root, anchor=None, taken=taken)
            placed += 1
        seen = {root.id}
        queue = [root]
        while queue:
            here = queue.pop(0)
            for other_id in sorted(links.get(group, {}).get(here.id, ()), key=str):
                if other_id in seen:
                    continue
                seen.add(other_id)
                other = by_id[other_id]
                if place_of(other) is None:
                    await assign(session, other, anchor=here, taken=taken)
                    placed += 1
                queue.append(other)
        #: What no edge reaches: round the group's first node, in one order on
        #: every server.
        for node in order:
            if place_of(node) is None:
                await assign(session, node, anchor=root, taken=taken)
                placed += 1
    return placed
