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
from src.units import METRES_PER_KM, PERCENT

log = logging.getLogger(__name__)

#: How many candidates the disc offers for every node wanted: enough that
#: a region three-fifths sea still fills its count on the land.
OVERSAMPLE = 12
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
    #: Which settled edge the site belongs to: the index of its centre.
    region: int = 0


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


def _disc(radius: float, centre: globe.Geo, reach_m: float, count: int) -> list[globe.Geo]:
    """Sunflower points over the disc of `reach_m` round `centre`: even, and
    the same every time."""
    golden = math.pi * (3 - math.sqrt(5))
    points = []
    for i in range(count):
        r = reach_m * math.sqrt((i + 0.5) / count)
        angle = i * golden
        points.append(globe.offset(radius, centre, r * math.cos(angle), r * math.sin(angle)))
    return points


def _first_land(constants: Constants, planet: Planet) -> globe.Geo | None:
    """The centre of a planet nobody seeded a city on: its first dry point."""
    lat_max = float(constants[R.MAP_CITY_LAT_MAX])
    for point in _spiral(OVERSAMPLE * OVERSAMPLE):
        if abs(point[0]) <= lat_max and terrain.is_land(constants, planet, *point):
            return point
    return None


def sites(
    constants: Constants,
    planet: Planet,
    count: int,
    *,
    taken: list[globe.Geo],
    centres: list[globe.Geo] | None = None,
) -> list[Site]:
    """Where the planet's wild nodes stand: round the seeded cities, on land,
    apart, and clear of what is pinned.

    **Round the cities**, not over the whole sphere (D-319, plan §6). A planet
    is drawn at its true size, and a hundred nodes spread over Terra stood
    eight hundred kilometres apart -- a month of walking per edge, and more
    stamina than a body has. The settled edge of a city is `map.region_km`
    round it, the nodes are laid inside that circle at an even spacing (a
    `map.spacing_share` of it kept between neighbours), and the rest of the
    sphere is ground with no node on it: sea, ice, the continents nobody has
    reached. A planet with no seeded city -- Pyroxis -- gets one region round
    its first dry point. Between regions there is no way on foot; that is
    the map's own "hours between regions" (50-interface/05), left to ships
    and to later decisions.
    """
    if count <= 0:
        return []
    radius = globe.radius_m(constants, planet)
    lat_max = float(constants[R.MAP_CITY_LAT_MAX])
    reach = float(constants[R.MAP_REGION_KM]) * METRES_PER_KM
    homes = list(centres or [])
    if not homes:
        first = _first_land(constants, planet)
        if first is None:
            log.warning("%s has no dry ground to lay a region on", planet.value)
            return []
        homes = [first]
    settled = math.pi * reach * reach * len(homes)
    spacing = math.sqrt(settled / count) * float(constants[R.MAP_SPACING_SHARE])
    per = -(-count * OVERSAMPLE // len(homes))
    discs = [_disc(radius, home, reach, per) for home in homes]
    chosen: list[Site] = []
    kept: list[globe.Geo] = list(taken)
    #: Region by region in turn, so the count is shared between the cities
    #: and the last sites -- the frozen cities' -- are spread over them too.
    for i in range(per):
        for region, disc in enumerate(discs):
            if len(chosen) >= count:
                break
            point = disc[i]
            if abs(point[0]) > lat_max or not terrain.is_land(constants, planet, *point):
                continue
            if any(globe.distance_m(radius, point, other) < spacing for other in kept):
                continue
            marks = terrain.marks_at(constants, planet, *point)
            chosen.append(Site(number=len(chosen) + 1, point=point, marks=marks, region=region))
            kept.append(point)
        if len(chosen) >= count:
            break
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


def ways(
    constants: Constants,
    planet: Planet,
    points: list[globe.Geo],
    *,
    regions: list[int] | None = None,
) -> list[tuple[int, int]]:
    """Which pairs are joined: the relative neighbourhood graph, cut by the
    relief, kept whole -- within a region. Two regions are two islands: no
    way is laid between settled edges, however the tree would like one."""
    n = len(points)
    if n < 2:
        return []
    of = regions or [0] * n
    radius = globe.radius_m(constants, planet)
    dist = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            dist[i][j] = dist[j][i] = globe.distance_m(radius, points[i], points[j])
    neighbours: list[tuple[int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            if of[i] != of[j]:
                continue
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
    The cities the layout pinned are the centres of the regions; every pinned
    place is kept clear of, and the wild ways are sewn to the cities' own
    nodes -- never to a city's delegate, which is a mark on the map and not
    ground to stand on (D-319 §1). Every city of the Forerunners on the
    planet, seeded or laid here, has all its rooms open from the first day.
    """
    if count <= 0 and lost_cities <= 0:
        return []
    laid_before = select(Node.id).where(
        (Node.key == _key(planet, 1)) | Node.key.like(f"{planet.value}.lost.%")
    )
    if await session.scalar(laid_before.limit(1)):
        return []
    surface = (
        await session.execute(select(Node).where(Node.layer == Layer.PLANET, Node.planet == planet))
    ).scalars()
    pinned = [node for node in surface if places.geo_of(node) is not None]
    parents = set(
        (
            await session.execute(select(Node.parent_id).where(Node.planet == planet).distinct())
        ).scalars()
    )
    delegates = [node for node in pinned if node.id in parents]
    leaves = [node for node in pinned if node.id not in parents]
    centres = [places.geo_of(node) for node in delegates]
    chosen = sites(
        constants,
        planet,
        count + lost_cities,
        taken=[places.geo_of(n) for n in pinned],
        centres=centres,
    )
    dice = random.Random(f"{planet.value}:surface")
    laid: list[Node] = []
    wild = chosen[: max(0, len(chosen) - lost_cities)]
    for site in wild:
        laid.append(await _wild_node(session, constants, catalog, planet, sphere, site, dice))
    for site in chosen[len(wild) :]:
        port = await ruins.lost_city(session, constants, laid[0] if laid else sphere, at=site.point)
        await _open_every_room(session, constants, port, dice)
        laid.append(port)
    for pier in leaves:
        if pier.key.endswith(".port") and await ruins.city_of(session, pier) is not None:
            await _open_every_room(session, constants, pier, dice)
    graph = leaves + laid
    radius = globe.radius_m(constants, planet)
    regions = [_region_of(radius, places.geo_of(node), centres) for node in leaves] + [
        site.region for site in chosen
    ]
    settled = {node.id for node in leaves}
    for i, j in ways(constants, planet, [places.geo_of(n) for n in graph], regions=regions):
        if graph[i].id in settled and graph[j].id in settled:
            continue
        await travel.connect(session, graph[i], graph[j], surface=Surface.WILD)
    await session.flush()
    log.info(
        "%s laid: %s wild nodes, %s cities of the Forerunners, %s regions",
        planet.value,
        len(wild),
        len(chosen) - len(wild),
        max(1, len(centres)),
    )
    return laid


def _region_of(radius: float, point: globe.Geo, centres: list[globe.Geo]) -> int:
    """Which settled edge a pinned place belongs to: its nearest city's."""
    if not centres:
        return 0
    return min(range(len(centres)), key=lambda k: globe.distance_m(radius, point, centres[k]))


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
