# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Where a node stands, once and for everybody (D-237, D-319).

A place is a property of the node, like its area:

* **assigned once**, when the node is created, and never recomputed. The world
  is eternal and has no wipes (D-007) -- a map that redrew itself would be the
  one thing in it that does;
* **next to the node it was laid from**. The anchor is passed by whoever
  creates the node -- a room knows the corridor it opened off, a ship's node
  knows the one it was laid from, the seed knows what it lays beside what --
  so an edge on the map is short and the graph reads as a graph.

Two kinds of place, for the two levels the graph has (D-319):

* **the surface is a sphere.** A node of a planet's surface stands at a
  latitude and a longitude (`globe`), one level for the whole planet: a
  house at the edge of a city and a vein in the taiga share one map, and the
  seat is searched on the tangent plane around the anchor in metres
  (`map.city_step_m`, `map.min_gap_m`), never nearer to anybody than the gap;
* **the inside is flat.** Floors of a house and rooms aboard a hull (the
  `location` level) stand at `x, y` in map units of their own group, as they
  always did: they have no north, and the client draws them in the window of
  the inside, not on the globe.

The client draws what it is given and computes nothing. That is the whole
point: one map for every player, the same one tomorrow, and the globe turns
only by latitude and longitude, north up.

## What is not placed here

The space layer. A planet's place is a function of time -- angle from the
world's epoch, radius from the vault (`world.orbit_of`) -- and a stored point
would be a second, lying opinion about where it is. A ship stands beside its
planet, which is the same rule.
"""

from __future__ import annotations

import hashlib
import math

from sqlalchemy import Float, cast, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src import globe
from src.constants import Constants, current
from src.constants import registry as R
from src.engine import props
from src.engine.errors import Refusal
from src.models.world import Layer, Node
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
#: "орбита" beside it -- and a `properties` key rather than a column, because a
#: place is a fact about one node and needs no index.
PLACE = "map"
#: The flat place of the inside: map units of the group.
PLACE_X = "x"
PLACE_Y = "y"
#: The place on the sphere: degrees.
PLACE_LAT = "lat"
PLACE_LON = "lon"

#: Where a group's first node stands when there is nothing to stand next to.
ORIGIN = (0.0, 0.0)
#: And on the sphere: the equator under the prime meridian. A city the seed
#: gives no point to stands here, and the seed is expected to give one.
ORIGIN_GEO: globe.Geo = (0.0, 0.0)


class PlaceIsFixed(Refusal):
    """A node of the world does not move: its place is given once (D-237)."""


#: The levels a place is stored for. Space is not among them: there a node's
#: point is computed from the clock, and a stored one would contradict it.
PLACED = (Layer.PLANET, Layer.LOCATION)


def _flat(properties: dict | None) -> tuple[float, float] | None:
    point = (properties or {}).get(PLACE)
    if not isinstance(point, dict) or PLACE_X not in point:
        return None
    try:
        return float(point[PLACE_X]), float(point[PLACE_Y])
    except (KeyError, TypeError, ValueError):  # pragma: no cover -- a hand-edited row
        return None


def _geo(properties: dict | None) -> globe.Geo | None:
    point = (properties or {}).get(PLACE)
    if not isinstance(point, dict) or PLACE_LAT not in point:
        return None
    try:
        return float(point[PLACE_LAT]), float(point[PLACE_LON])
    except (KeyError, TypeError, ValueError):  # pragma: no cover -- a hand-edited row
        return None


def place_of(node: Node) -> tuple[float, float] | None:
    """The node's flat place -- a floor's, a room's -- or None."""
    return _flat(node.properties)


def degrees(which: str):
    """The node's latitude or longitude as SQL, for a window over the surface.

    Spelled with the `->` and `->>` operators and not the subscript SQLAlchemy
    writes for `properties["map"]`: the index of `Node` is declared over the
    operator form, and Postgres matches an index by the shape of the
    expression -- a subscript would read the whole table past it.
    """
    return cast(Node.properties.op("->")(PLACE).op("->>")(which), Float)


def geo_of(node: Node) -> globe.Geo | None:
    """The node's place on its planet's sphere, or None: the sky, or the inside."""
    return _geo(node.properties)


def beside(constants: Constants, anchor: Node, key: str) -> globe.Geo | None:
    """A point one gap from a node, in a direction of `key`'s own.

    The one place that steps off the rings this module lays without searching
    for a free seat (`_geo_seat`). It is for what is **not** a node of the
    world and never will be: a hull moored at a pier, which the map has to
    draw somewhere and which sails away again (`ship.view.sight`). A view may
    not reserve anything -- it answers a question -- so this walks out by the
    gap and takes what it finds, and a hull may end up beside a node's mark.
    That is the price of not writing, and it is paid here rather than in the
    view, so that the module which owns the gap owns its one exception too.

    Exactly **one** gap out, whoever asks: `map.min_gap_m` is the floor on how
    near two things stand, and walking out by multiples of it would carry the
    tenth hull across a city thirty metres wide. What differs between two of
    them is the direction, read off the key the way a node's lean is
    (`_direction`) -- stable for ever (D-007), so the same hull is drawn in
    the same spot for everybody, and two hulls at one pier do not coincide
    even where they share a berth.

    None where the anchor is not on a sphere at all: the sky, or an inside.
    """
    at = _geo(anchor.properties)
    if at is None:
        return None
    gap = float(constants[R.MAP_MIN_GAP_M])
    turn = _direction(key)
    radius = globe.radius_m(constants, anchor.planet)
    return globe.offset(radius, at, gap * math.cos(turn), gap * math.sin(turn))


def wire_geo(point: globe.Geo) -> dict[str, float]:
    """A point on the sphere as the client is told it.

    The one spelling of a place on the wire, so that a node which has one and
    a view which lends one (a hull at its pier, `ship.view.sight`) cannot
    drift apart in the keys they use.
    """
    return {"lat": point[0], "lon": point[1]}


def wire(node: Node) -> dict[str, float] | None:
    """The place as the client is told it. The data key is the world's, this one is the code's."""
    on_sphere = geo_of(node)
    if on_sphere is not None:
        return wire_geo(on_sphere)
    flat = place_of(node)
    return None if flat is None else {"x": flat[0], "y": flat[1]}


def _direction(key: str) -> float:
    """Which way this node leans off its anchor -- its own, and always the same.

    Read off the key rather than rolled: two servers replaying the same world
    must lay the same map, and a node keeps its key for ever (D-007).
    """
    seed = 0
    for letter in key:
        seed = (seed * MAP_HASH_STEP + ord(letter)) % MAP_HASH_SPAN
    return (seed / MAP_HASH_SPAN) * math.tau


def _group(node: Node) -> tuple[object, ...]:
    """Which map this node is drawn on: one planet's surface, or one house's floors."""
    if node.layer is Layer.PLANET:
        return (node.layer, node.planet)
    return (node.layer, node.parent_id)


async def _hold(session: AsyncSession, node: Node) -> None:
    """Hold this node's map until the transaction ends.

    Two rooms opened at once, two nodes the seed lays in one breath: both read
    the same taken places and both pick the same free spot. A place is never
    recomputed, so an overlap made here would be permanent and there would be
    nothing left to fix it with. The lock is per group -- one planet's surface,
    one house -- so laying out Terra does not wait on laying out Aurora.
    """
    stamp = hashlib.blake2b(str(_group(node)).encode(), digest_size=MAP_LOCK_BYTES).digest()
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int.from_bytes(stamp, "big", signed=True)},
    )


# --- the inside: flat --------------------------------------------------------


async def _flat_neighbourhood(session: AsyncSession, node: Node) -> list[tuple[float, float]]:
    """The flat places already taken in this node's group: the rooms of one house."""
    rows = await session.execute(
        select(Node.id, Node.properties).where(
            Node.layer == node.layer, Node.parent_id == node.parent_id
        )
    )
    taken = []
    for other_id, properties in rows:
        if other_id == node.id:
            continue
        point = _flat(properties)
        if point is not None:
            taken.append(point)
    return taken


async def _flat_centre(
    session: AsyncSession, node: Node, anchor: Node | None
) -> tuple[float, float]:
    """What the new room is laid next to: the room it opened off, on the same floor plan."""
    cursor = anchor
    while cursor is not None:
        if cursor.layer is node.layer:
            return place_of(cursor) or ORIGIN
        if cursor.parent_id is None:
            break
        cursor = await session.get(Node, cursor.parent_id)
    return ORIGIN


def _flat_free(spot: tuple[float, float], taken: list[tuple[float, float]]) -> bool:
    return all(math.hypot(spot[0] - x, spot[1] - y) >= MAP_MIN_GAP for x, y in taken)


def _flat_seat(
    centre: tuple[float, float], taken: list[tuple[float, float]], lean: float
) -> tuple[float, float]:
    """A free flat place on some ring round the centre. Never one already taken.

    How many seats a ring has is its own circumference's business, and the
    walk out is bounded: past the bound the node is seated a whole step beyond
    everything on the map, which cannot fail the gap.
    """
    rings = len(taken) + MAP_RINGS
    for ring in range(1, rings + 1):
        radius = MAP_STEP * ring
        for seat in range(max(1, int(math.tau * radius / MAP_MIN_GAP))):
            angle = lean + MAP_TURN * seat
            spot = (centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle))
            if _flat_free(spot, taken):
                return spot
    edge = max(  # pragma: no cover -- a gap wider than the rings it is measured on
        (math.hypot(x - centre[0], y - centre[1]) for x, y in taken), default=0.0
    )
    far = edge + MAP_STEP  # pragma: no cover
    return (centre[0] + far * math.cos(lean), centre[1] + far * math.sin(lean))  # pragma: no cover


# --- the surface: a sphere ----------------------------------------------------


async def _geo_neighbourhood(session: AsyncSession, node: Node) -> list[globe.Geo]:
    """The places already taken on this node's planet: the whole surface is one map (D-319).

    Two columns, not whole rows: a planet's surface has no ceiling, and this
    runs on every node created.
    """
    rows = await session.execute(
        select(Node.id, Node.properties).where(
            Node.layer == Layer.PLANET, Node.planet == node.planet
        )
    )
    taken = []
    for other_id, properties in rows:
        if other_id == node.id:
            continue
        point = _geo(properties)
        if point is not None:
            taken.append(point)
    return taken


async def _geo_centre(session: AsyncSession, anchor: Node | None) -> globe.Geo:
    """What the new node is laid next to: the anchor's nearest ancestor standing on the sphere."""
    cursor = anchor
    while cursor is not None:
        point = geo_of(cursor)
        if point is not None:
            return point
        if cursor.parent_id is None:
            break
        cursor = await session.get(Node, cursor.parent_id)
    return ORIGIN_GEO


def _geo_free(radius: float, gap: float, spot: globe.Geo, taken: list[globe.Geo]) -> bool:
    return all(globe.distance_m(radius, spot, other) >= gap for other in taken)


def _geo_seat(
    constants: Constants,
    radius: float,
    centre: globe.Geo,
    taken: list[globe.Geo],
    lean: float,
) -> globe.Geo:
    """A free place on some ring round the centre, in metres on the tangent plane.

    The same walk as the flat one -- rings a step apart, the golden angle
    between tries, as many tries as the ring can hold -- only the ring is
    measured in metres on the sphere, and a seat is free when it is a gap or
    more from everybody, by great circle.
    """
    step = float(constants[R.MAP_CITY_STEP_M])
    gap = float(constants[R.MAP_MIN_GAP_M])
    rings = len(taken) + MAP_RINGS
    for ring in range(1, rings + 1):
        reach = step * ring
        for seat in range(max(1, int(math.tau * reach / gap))):
            angle = lean + MAP_TURN * seat
            spot = globe.offset(radius, centre, reach * math.cos(angle), reach * math.sin(angle))
            if _geo_free(radius, gap, spot, taken):
                return spot
    edge = max(  # pragma: no cover -- a gap wider than the rings it is measured on
        (globe.distance_m(radius, centre, other) for other in taken), default=0.0
    )
    far = edge + step  # pragma: no cover
    return globe.offset(
        radius, centre, far * math.cos(lean), far * math.sin(lean)
    )  # pragma: no cover


# --- the one door --------------------------------------------------------------


async def assign(
    session: AsyncSession,
    node: Node,
    *,
    anchor: Node | None = None,
    taken: list | None = None,
) -> None:
    """Give the node its place, next to the anchor and clear of everybody else.

    Called once, from `world.create_node`. A node that already has a place
    keeps it: this is the one property of a node that never moves. A place
    written before creation -- the seed's pin -- is such a place.

    `taken` is the group's occupied places when the caller already holds them;
    left out, they are read here, under the group's lock.
    """
    if node.layer not in PLACED or place_of(node) is not None or geo_of(node) is not None:
        return
    if node.layer is Layer.PLANET:
        if taken is None:
            await _hold(session, node)
            taken = await _geo_neighbourhood(session, node)
        constants = current()
        radius = globe.radius_m(constants, node.planet)
        centre = await _geo_centre(session, anchor)
        gap = float(constants[R.MAP_MIN_GAP_M])
        #: The surface's own first node stands at its origin: there is nothing
        #: to stand beside.
        spot = (
            centre
            if _geo_free(radius, gap, centre, taken)
            else _geo_seat(constants, radius, centre, taken, _direction(node.key))
        )
        taken.append(spot)
        await props.stamp(session, node, {PLACE: {PLACE_LAT: spot[0], PLACE_LON: spot[1]}})
        return
    if taken is None:
        await _hold(session, node)
        taken = await _flat_neighbourhood(session, node)
    centre = await _flat_centre(session, node, anchor)
    spot = centre if _flat_free(centre, taken) else _flat_seat(centre, taken, _direction(node.key))
    taken.append(spot)
    await props.stamp(session, node, {PLACE: {PLACE_X: spot[0], PLACE_Y: spot[1]}})


async def pin(session: AsyncSession, node: Node, point: globe.Geo) -> None:
    """Put a surface node at this point on its sphere: the seed's pin, before any seat is searched.

    The one way a place on the sphere is chosen rather than found: the
    scenario names where a city stands, and everything laid beside it follows.
    Refused once the node has a place (D-237).
    """
    if node.layer is not Layer.PLANET:
        raise PlaceIsFixed(key="place-is-fixed", node=node.name)
    if geo_of(node) is not None:
        raise PlaceIsFixed(key="place-is-fixed", node=node.name)
    await _hold(session, node)
    await props.stamp(session, node, {PLACE: {PLACE_LAT: point[0], PLACE_LON: point[1]}})


async def move(session: AsyncSession, node: Node, spot: tuple[float, float]) -> None:
    """Put an existing room at this flat place -- the one way a place ever changes.

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
        raise PlaceIsFixed(key="place-is-fixed", node=node.name)
    await _hold(session, node)
    await props.stamp(session, node, {PLACE: {PLACE_X: spot[0], PLACE_Y: spot[1]}})


def distance_m(constants: Constants, one: Node, other: Node) -> float | None:
    """How far apart two surface nodes stand, in metres -- or None off the sphere.

    Two planets share no ground: a pair on different planets has no distance,
    like a pair of which one is a room or a hull.
    """
    here, there = geo_of(one), geo_of(other)
    if here is None or there is None or one.planet is not other.planet:
        return None
    return globe.distance_m(globe.radius_m(constants, one.planet), here, there)
