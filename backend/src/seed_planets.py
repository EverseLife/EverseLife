# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The whole surface of a planet, laid at the world's birth (D-319).

Nothing is found any more: every wild node and every way between them exists
from the first day and is walked, not discovered. This module lays them --
where the relief allows, as far apart as the planet's size and the vault's
count make room for, joined by the ways a walker would take, with the
chokepoints the relief itself draws.

## Sites

Candidates come off a spiral that covers the sphere evenly (the Fibonacci
lattice), several for every node wanted; a candidate stands only on land, on
this side of the last latitude, and no nearer to any earlier site -- or to a
node the layout already pinned -- than the spacing. The spacing is the
planet's: the land's area over the count wanted, so a small dry world packs
its nodes as densely as a large wet one. Deterministic from the spiral and
the relief, so two servers lay one world.

## Ways

The relative neighbourhood graph over the sites: two nodes are joined when
no third stands nearer to both than they stand to each other -- the graph a
walker draws by going to the nearest places first. Then the relief cuts:
a way that crosses the sea or a lake, or climbs over the mountain line, is
dropped -- **unless** it is one of the ways the surface needs to stay one
piece, which are kept whatever they cross. Those are the fords and the
passes, and there are as few of them as connectivity allows: the chokepoints
of 10-world/07, drawn by the ground rather than by a hand.

## What a node is

Its marks are the relief's at its point -- river water, mountain, woods,
stones, meadow -- and its climate the field's (`terrain`); a node in the
mountains bears a vein `terrain.mountain_vein_k` times as often as one on
the plain, the species by the planet's own pace (`ground.species_of`). On a
planet of the Forerunners some sites are cities of theirs, frozen and dark,
with every room already open: the finding is gone, the digging remains
(D-232, 10-world/05).
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import globe
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import ground, places, ruins, terrain, travel, world
from src.models.world import Layer, Node, Planet, Surface
from src.units import PERCENT

log = logging.getLogger(__name__)

#: How many candidates the spiral offers for every node wanted: enough that
#: a planet three-fifths sea still fills its count on the land.
OVERSAMPLE = 12
#: The share of the even spacing two sites keep between them. Under one, so
#: the count can be met on land the sea and the mountains have eaten into.
SPACING_SHARE = 0.6
#: How many points along a way are read for water and mountains.
WAY_SAMPLES = 8
#: The names a wild node is born with, by what the relief made it. Russian
#: literals in a row the locale cannot reach -- the same wave-IV debt as every
#: name the seed writes (`seed_surfaces`), and read by the client through
#: the node's name like any other.
NAMES = {
    "river": "Речная пойма",
    "mountain": "Горный склон",
    "woods": "Роща",
    "meadow": "Луг",
    "stones": "Каменистая пустошь",
    "plain": "Дикий участок",
}


@dataclass(frozen=True, slots=True)
class Site:
    number: int
    point: globe.Geo
    marks: dict


def _spiral(count: int) -> list[globe.Geo]:
    """The Fibonacci lattice: `count` points evenly over the sphere, in degrees."""
    golden = math.pi * (3 - math.sqrt(5))
    points = []
    for i in range(count):
        z = 1 - (2 * i + 1) / count
        lat = math.degrees(math.asin(z))
        lon = math.degrees((i * golden) % math.tau) - 180.0
        points.append((lat, lon))
    return points


def _land_area(constants: Constants, planet: Planet) -> float:
    radius = globe.radius_m(constants, planet)
    field = terrain.field_of(constants, planet)
    return 4 * math.pi * radius * radius * max(field.land_share(), 1e-6)


def sites(
    constants: Constants, planet: Planet, count: int, *, taken: list[globe.Geo]
) -> list[Site]:
    """Where the planet's wild nodes stand: on land, apart, and clear of what is pinned."""
    if count <= 0:
        return []
    radius = globe.radius_m(constants, planet)
    spacing = math.sqrt(_land_area(constants, planet) / count) * SPACING_SHARE
    chosen: list[Site] = []
    kept: list[globe.Geo] = list(taken)
    for point in _spiral(count * OVERSAMPLE):
        if len(chosen) >= count:
            break
        if not terrain.is_land(constants, planet, *point):
            continue
        if any(globe.distance_m(radius, point, other) < spacing for other in kept):
            continue
        marks = terrain.marks_at(constants, planet, *point)
        chosen.append(Site(number=len(chosen) + 1, point=point, marks=marks))
        kept.append(point)
    if len(chosen) < count:
        log.warning("%s has land for %s of the %s nodes asked", planet.value, len(chosen), count)
    return chosen


def _name_of(marks: dict) -> str:
    if marks.get(world.WATER) == world.RIVER:
        return NAMES["river"]
    for key in ("mountain", "woods", "meadow", "stones"):
        if marks.get(key if key != "mountain" else terrain.MOUNTAIN):
            return NAMES[key]
    return NAMES["plain"]


def _crosses(constants: Constants, planet: Planet, a: globe.Geo, b: globe.Geo) -> str | None:
    """What a straight way between two points crosses: water, a mountain, or nothing."""
    field = terrain.field_of(constants, planet)
    for step in range(1, WAY_SAMPLES):
        share = step / WAY_SAMPLES
        lat = a[0] + (b[0] - a[0]) * share
        dlon = ((b[1] - a[1] + 180.0) % 360.0) - 180.0
        lon = ((a[1] + dlon * share + 180.0) % 360.0) - 180.0
        if field.is_water(lat, lon):
            return "water"
        if field.is_mountain(lat, lon):
            return "mountain"
    return None


def ways(constants: Constants, planet: Planet, points: list[globe.Geo]) -> list[tuple[int, int]]:
    """Which pairs are joined: the relative neighbourhood graph, cut by the relief, kept whole."""
    n = len(points)
    if n < 2:
        return []
    radius = globe.radius_m(constants, planet)
    dist = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            dist[i][j] = dist[j][i] = globe.distance_m(radius, points[i], points[j])
    neighbours: list[tuple[int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            d = dist[i][j]
            if all(not (dist[i][k] < d and dist[j][k] < d) for k in range(n) if k not in (i, j)):
                neighbours.append((i, j))
    #: The ways the surface needs to stay one piece: a minimum spanning tree
    #: where a crossing costs dearly, so the tree fords a river only where no
    #: dry way exists -- and those fords are the chokepoints.
    crossing = {
        pair: _crosses(constants, planet, points[pair[0]], points[pair[1]]) for pair in neighbours
    }
    weight = {
        pair: dist[pair[0]][pair[1]] * (1.0 if crossing[pair] is None else float(n))
        for pair in neighbours
    }
    parent = list(range(n))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    kept: set[tuple[int, int]] = set()
    for pair in sorted(neighbours, key=lambda p: weight[p]):
        a, b = root(pair[0]), root(pair[1])
        if a != b:
            parent[a] = b
            kept.add(pair)
    return [pair for pair in neighbours if crossing[pair] is None or pair in kept]


async def lay(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    planet: Planet,
    *,
    sphere: Node,
    count: int,
    lost_cities: int = 0,
) -> list[Node]:
    """Lay the planet's wild nodes and the ways between them, once.

    Idempotent by key: a world that has its nodes gets nothing laid again.
    The nodes the layout pinned on this planet are kept clear of and joined
    into the ways, so the capital's surroundings are one graph with the rest.
    """
    if count <= 0 and lost_cities <= 0:
        return []
    if await session.scalar(select(Node.id).where(Node.key == _key(planet, 1)).limit(1)):
        return []
    pinned = [
        node
        for node in (
            await session.execute(
                select(Node).where(
                    Node.layer == Layer.PLANET, Node.planet == planet, Node.parent_id == sphere.id
                )
            )
        ).scalars()
        if places.geo_of(node) is not None
    ]
    chosen = sites(constants, planet, count + lost_cities, taken=[places.geo_of(n) for n in pinned])
    dice = random.Random(f"{planet.value}:surface")
    laid: list[Node] = []
    wild = chosen[: max(0, len(chosen) - lost_cities)]
    for site in wild:
        laid.append(await _wild_node(session, constants, catalog, planet, sphere, site, dice))
    for site in chosen[len(wild) :]:
        port = await ruins.lost_city(session, constants, laid[0] if laid else sphere, at=site.point)
        await _open_every_room(session, constants, port, dice)
        laid.append(port)
    graph = pinned + laid
    for i, j in ways(constants, planet, [places.geo_of(n) for n in graph]):
        await travel.connect(session, graph[i], graph[j], surface=Surface.WILD)
    await session.flush()
    log.info(
        "%s laid: %s wild nodes, %s cities of the Forerunners",
        planet.value,
        len(wild),
        len(chosen) - len(wild),
    )
    return laid


def _key(planet: Planet, number: int) -> str:
    return f"{planet.value}.wild.{number:03d}"


async def _wild_node(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    planet: Planet,
    sphere: Node,
    site: Site,
    dice: random.Random,
) -> Node:
    vein_share = float(constants[R.EXPLORE_VEIN_SHARE]) / PERCENT
    if site.marks.get(terrain.MOUNTAIN):
        vein_share = min(1.0, vein_share * float(constants[R.TERRAIN_MOUNTAIN_VEIN_K]))
    bears_vein = dice.random() < vein_share
    area = constants[R.EXPLORE_NODE_AREA]
    properties = await ground.properties(
        session, constants, dice, vein=bears_vein, at=(planet, site.point)
    )
    properties[places.PLACE] = {places.PLACE_LAT: site.point[0], places.PLACE_LON: site.point[1]}
    node = await world.create_node(
        session,
        _key(planet, site.number),
        _name_of(site.marks),
        planet=planet,
        area_m2=dice.uniform(area.min, area.max),
        layer=Layer.PLANET,
        parent=sphere,
        properties=properties,
    )
    if bears_vein:
        species = await ground.species_of(session, constants, catalog, dice, planet=planet)
        richness = constants[R.EXPLORE_VEIN_RICHNESS]
        stock = constants[R.EXPLORE_VEIN_STOCK]
        await world.create_vein(
            session,
            node,
            species,
            richness=dice.uniform(richness.min, richness.max),
            remaining=dice.uniform(stock.min, stock.max),
        )
    return node


async def _open_every_room(
    session: AsyncSession, constants: Constants, port: Node, dice: random.Random
) -> None:
    """Every room of a city of the Forerunners is open from the first day (D-319).

    Deeper and deeper off the hall: one corridor, as a digger would have
    opened it, so the depth of a room still means what it meant.
    """
    city = await ruins.city_of(session, port)
    if city is None:  # pragma: no cover -- a pier is always a city's
        return
    origin = port
    hall = await session.scalar(select(Node).where(Node.key == f"{city.key}.hall"))
    if hall is not None:
        origin = hall
    while not ruins.exhausted(constants, city):
        origin = await ruins.open_room(session, constants, dice, origin)
        await session.refresh(city)


#: Which planets the seed lays a surface on, and from which keys the counts come.
COUNTS = {
    Planet.TERRA: R.MAP_NODES_TERRA,
    Planet.AURORA: R.MAP_NODES_AURORA,
    Planet.PYROXIS: R.MAP_NODES_PYROXIS,
    Planet.AQUATICA: R.MAP_NODES_AQUATICA,
}


async def lay_all(session: AsyncSession, constants: Constants, catalog: Catalog) -> None:
    """Every planet's surface, in the order the vault lists them."""
    for planet, key in COUNTS.items():
        sphere = await session.scalar(select(Node).where(Node.key == planet.value))
        if sphere is None:  # pragma: no cover -- the system is laid before the surfaces
            continue
        lost = int(constants[R.RUINS_LOST_CITIES].get(planet.value, 0))
        await lay(
            session,
            constants,
            catalog,
            planet,
            sphere=sphere,
            count=int(constants[key]),
            lost_cities=lost,
        )
