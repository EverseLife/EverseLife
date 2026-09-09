# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Whether a scout may aim here: the landscape's rules of reach, direction and room (D-321).

The scout points at the globe; the game answers with the landscape. Three
questions, each its own refusal, so the player learns which rule stood in the
way:

* **reach** -- the biome of the node the scout stands in says how near and how
  far one may aim (`biome.reach_m`): on the plain close and often, in the
  mountains farther and seldom;
* **direction** -- the aim is dry ground, and the straight way to it crosses no
  water: from the shore one does not aim at the sea, and a river is crossed
  where a ford is found, not by pointing across it;
* **room** -- the found node overlaps no node already standing (the two radii
  by area), and the new way crosses no existing way. Where
  everything round a city is found, nothing can be aimed at any more -- the
  land is exhausted by its geometry, not by a counter.

All of it is read off two columns of the planet's surface nodes and their
ways; a planet's surface has no ceiling, and this runs on every aim, so the
read stays narrow.
"""

from __future__ import annotations

import math
import uuid

import numpy as np
from sqlalchemy import or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from src import field as fields
from src import globe
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import access, biome, facet, places, terrain
from src.engine.biome import word_of
from src.engine.explore._base import (
    Aim,
    CrossesWay,
    IntoWater,
    NoRoom,
    NotFromHere,
    NotLand,
    Shut,
    TooFar,
    TooNear,
    cell_of,
    point_of,
)
from src.models.identity import Body
from src.models.world import Edge, Layer, Node, Planet
from src.units import METRES_PER_KM


def radius_of(area_m2: float) -> float:
    """The radius of the circle a node's area makes: its footprint on the map.
    The arithmetic is `globe`'s, so what reads the placement rule from
    elsewhere (`facet.room_floor`) does not reach into the aim for it."""
    return globe.radius_of_area(area_m2)


def area_for(constants: Constants, free_m: float) -> float | None:
    """The area a find takes from the room round it (D-321, the owner's rule of
    2026-09-06): a long leap lands on wide ground, a short one on a patch.

    The find fills `explore.fill_share` of the free radius -- the distance to
    the nearest standing node's edge -- and is bounded by `explore.node_area`:
    above the ceiling the rest stays open ground, below the floor there is no
    room for a node at all and the aim is refused. The exclusion round a node
    is therefore its own circle: the wider the node, the farther the next
    scout must aim.
    """
    span = constants[R.EXPLORE_NODE_AREA]
    radius = float(constants[R.EXPLORE_FILL_SHARE]) * free_m
    if radius < radius_of(span.min):
        return None
    return min(span.max, math.pi * radius * radius)


def crosses_water(
    constants: Constants, planet: Planet, a: globe.Geo, b: globe.Geo, *, ford: bool = False
) -> bool:
    """Whether the straight way between two points crosses water.

    Sea and lake are read along the way; a river is a line the way may cut
    (`relief.Field.river_crossed`), and it may be cut only from or to a
    **ford** (D-321): until the field draws rivers finer than a cell
    (OQ-146), the ford is a mark a complex lays, not a place on the line.
    """
    field = terrain.field_of(constants, planet)
    #: The whole way in one ask of the field: a cell of the equal-area grid
    #: costs the call, not the sums (`field.cells_at`).
    share = np.arange(1, globe.WAY_SAMPLES) / globe.WAY_SAMPLES
    water = field.water[field.cells_at(*globe.walk_between(a, b, share))]
    if ((water == fields.SEA) | (water == fields.LAKE)).any():
        return True
    return not ford and field.river_crossed(a, b)


def _flat(radius: float, origin: globe.Geo, point: globe.Geo) -> tuple[float, float]:
    """A point in metres on the plane tangent at the origin."""
    return (
        math.radians(globe.wrap_lon(point[1] - origin[1])) * radius / globe.lon_stretch(origin[0]),
        math.radians(point[0] - origin[0]) * radius,
    )


def _side(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def segments_cross(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    """Whether the open segments a-b and c-d cross. Sharing an end is not crossing."""
    if a in (c, d) or b in (c, d):
        return False
    s1, s2 = _side(a, b, c), _side(a, b, d)
    s3, s4 = _side(c, d, a), _side(c, d, b)
    return (s1 > 0) != (s2 > 0) and (s3 > 0) != (s4 > 0) and 0 not in (s1, s2, s3, s4)


#: The mark a complex writes on a crossing (`explore.run.FORD`); read here by
#: name to keep the floor below the run.
FORD_MARK = "ford"


def _is_ford(node: Node) -> bool:
    return bool((node.properties or {}).get(FORD_MARK))


async def _surface(
    session: AsyncSession, constants: Constants, planet: Planet, around: globe.Geo
) -> list[tuple[Node, globe.Geo]]:
    """The placed nodes of the planet's surface within the aim's window of
    `around` (`explore.window_km`), with where they stand.

    Read by degrees in SQL over the place the node carries, so that an aim
    costs a window of the surface and not the surface: the finds of a planet
    grow without bound, and the room round one point does not.
    """
    radius = globe.radius_m(constants, planet)
    span = float(constants[R.EXPLORE_WINDOW_KM]) * METRES_PER_KM
    d_lat = math.degrees(span / radius)
    d_lon = d_lat * globe.lon_stretch(around[0])
    lat = places.degrees(places.PLACE_LAT)
    lon = places.degrees(places.PLACE_LON)
    low, high = around[1] - d_lon, around[1] + d_lon
    #: The window may straddle the antimeridian: then it is two ranges.
    if high - low >= globe.FULL_TURN:
        across = true()
    elif low < -globe.HALF_TURN:
        across = or_(lon >= low + globe.FULL_TURN, lon <= high)
    elif high > globe.HALF_TURN:
        across = or_(lon >= low, lon <= high - globe.FULL_TURN)
    else:
        across = lon.between(low, high)
    rows = (
        await session.execute(
            select(Node).where(
                Node.layer == Layer.PLANET,
                Node.planet == planet,
                lat.between(around[0] - d_lat, around[0] + d_lat),
                across,
            )
        )
    ).scalars()
    placed = []
    for node in rows:
        point = places.geo_of(node)
        if point is not None:
            placed.append((node, point))
    return placed


async def _ways_among(session: AsyncSession, nodes: list[Node]) -> list[Edge]:
    """The ways with at least one end among the nodes: a long way from
    outside the window crosses it as surely as a short one inside."""
    ids = [node.id for node in nodes]
    if not ids:
        return []
    return list(
        (
            await session.execute(
                select(Edge).where(or_(Edge.node_a_id.in_(ids), Edge.node_b_id.in_(ids)))
            )
        ).scalars()
    )


async def _far_ends(
    session: AsyncSession, edges: list[Edge], known: set[uuid.UUID]
) -> dict[uuid.UUID, globe.Geo]:
    """Where the ends of the ways outside the window stand."""
    missing = {
        end for edge in edges for end in (edge.node_a_id, edge.node_b_id) if end not in known
    }
    if not missing:
        return {}
    rows = (await session.execute(select(Node).where(Node.id.in_(missing)))).scalars()
    out: dict[uuid.UUID, globe.Geo] = {}
    for node in rows:
        point = places.geo_of(node)
        if point is not None:
            out[node.id] = point
    return out


async def check(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    origin: Node,
    target: globe.Geo,
    *,
    body: Body | None = None,
) -> Aim:
    """A lawful aim from `origin` at `target`, or the refusal that stands in the way.

    `body` is who is aiming, and it is asked about one thing only: the door at
    the far end. A run ends standing where it went (D-185, D-327), so the cell
    a body may not enter is a cell it may not aim at -- otherwise a survey
    would put it inside somebody's shut place, which no road may do (D-199).
    Asked here rather than in `survey` because the same question must be put
    again when the run is over: the world moves while the scout walks, and a
    door shut in the meantime must stop the arrival as surely as the aim.
    """
    origin_point = places.geo_of(origin)
    if origin.layer is not Layer.PLANET or origin_point is None:
        raise NotFromHere(key="explore-not-from-here")
    planet = origin.planet
    here = biome.of_node(constants, origin)
    if here is None:  # pragma: no cover -- a surface node stands on land by construction
        raise NotFromHere(key="explore-not-from-here")
    radius = globe.radius_m(constants, planet)
    cell = cell_of(constants, planet, target)
    point = point_of(constants, planet, cell)
    metres = globe.distance_m(radius, origin_point, point)
    near, far = facet.reach_m(constants, here, facet.of_node(constants, catalog, origin))
    if metres < near:
        raise TooNear(key="explore-too-near", metres=round(metres), near=round(near))
    if metres > far:
        raise TooFar(key="explore-too-far", metres=round(metres), far=round(far))
    if not terrain.is_land(constants, planet, *point):
        raise NotLand(key="explore-not-land")
    if crosses_water(constants, planet, origin_point, point, ford=_is_ford(origin)):
        raise IntoWater(key="explore-into-water")

    placed = await _surface(session, constants, planet, point)
    #: Whatever stands in the cell is the cell's node -- a find with the cell's
    #: key or a seeded place the layout pinned there (D-237): the second scout
    #: joins it rather than laying a twin beside it.
    existing = next(
        (node for node, where in placed if cell_of(constants, planet, where) == cell), None
    )
    if (
        existing is not None
        and body is not None
        and not await access.may_enter(session, existing, body.identity_id)
    ):
        raise Shut(key="explore-shut", node=word_of(constants, existing))
    area = float(existing.area_m2) if existing is not None else None
    if existing is None:
        #: The room there is: the distance to the nearest standing node's
        #: edge, the origin's included -- a find takes its share of it
        #: (`area_for`), and where the share is below a node's floor the
        #: ground is taken. The exclusion round a node is its own circle.
        free = math.inf
        nearest = origin
        for node, where in placed:
            room = globe.distance_m(radius, where, point) - radius_of(node.area_m2)
            if room < free:
                free, nearest = room, node
        area = area_for(constants, free)
        if area is None:
            raise NoRoom(key="explore-no-room", node=word_of(constants, nearest))
    #: The new way against every way of the surface, on the plane tangent at
    #: the origin: two ways that cross would make a crossroads nobody stands at.
    flat = {node.id: _flat(radius, origin_point, where) for node, where in placed}
    a, b = (0.0, 0.0), _flat(radius, origin_point, point)
    ways = await _ways_among(session, [node for node, _ in placed])
    for node_id, where in (await _far_ends(session, ways, set(flat))).items():
        flat[node_id] = _flat(radius, origin_point, where)
    for edge in ways:
        if origin.id in (edge.node_a_id, edge.node_b_id):
            continue
        if existing is not None and existing.id in (edge.node_a_id, edge.node_b_id):
            continue
        ends = (flat.get(edge.node_a_id), flat.get(edge.node_b_id))
        if ends[0] is None or ends[1] is None:  # pragma: no cover -- an end with no place
            continue
        if segments_cross(a, b, ends[0], ends[1]):
            raise CrossesWay(key="explore-crosses-way")
    return Aim(
        origin_id=origin.id,
        planet=planet,
        cell=cell,
        point=point,
        metres=metres,
        biome=here,
        existing=existing,
        area=area,
    )
