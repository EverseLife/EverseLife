# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The whole surface of a planet is laid at the world's birth (D-319).

Nothing is found any more: the wild nodes, the ways between them, the veins
and the frozen cities exist from the first day. What is checked here:

* the count the vault asks for is laid, on land, apart, clear of what the
  layout pinned, and inside the settled edge round a city -- close enough
  that every way is within a body's strength;
* the ways make one piece of the surface, and they cross water and mountains
  only where the surface would otherwise fall apart -- those are the fords;
* a node carries the relief's marks and the field's climate, and the
  mountains bear veins more often than the plain;
* a city of the Forerunners is laid frozen, dark and with every room open;
* the seed lays a planet once: a second run adds nothing.
"""

from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src import globe, seed_planets
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import places, ruins, terrain, world
from src.models.world import Edge, Layer, Node, Planet, Vein
from src.units import METRES_PER_KM

COUNT = 40


async def _sphere(session: AsyncSession, planet: Planet = Planet.TERRA) -> Node:
    return await world.create_node(
        session, planet.value, planet.value.title(), area_m2=1, planet=planet, layer=Layer.SPACE
    )


async def _laid(
    session: AsyncSession, constants: Constants, catalog: Catalog, *, lost: int = 0
) -> tuple[Node, list[Node]]:
    sphere = await _sphere(session)
    nodes = await seed_planets.lay(
        session, constants, catalog, Planet.TERRA, sphere=sphere, count=COUNT, lost_cities=lost
    )
    return sphere, nodes


def test_sites_stand_on_land_and_apart(constants: Constants) -> None:
    """Every site is dry, this side of the last latitude, and the spacing from every other."""
    sites = seed_planets.sites(constants, Planet.TERRA, COUNT, taken=[])
    assert len(sites) == COUNT
    radius = globe.radius_m(constants, Planet.TERRA)
    for site in sites:
        assert terrain.is_land(constants, Planet.TERRA, *site.point)
        assert abs(site.point[0]) <= constants[R.MAP_CITY_LAT_MAX]
    spacing = min(
        globe.distance_m(radius, a.point, b.point)
        for i, a in enumerate(sites)
        for b in sites[i + 1 :]
    )
    assert spacing > 0
    #: Round the region's centre, not over the sphere (plan §6): the first dry
    #: point stands in for a city on a planet nobody seeded one on.
    centre = seed_planets._first_land(constants, Planet.TERRA)
    assert centre is not None
    reach = constants[R.MAP_REGION_KM] * METRES_PER_KM
    assert all(globe.distance_m(radius, s.point, centre) <= reach for s in sites)
    #: Twice the same asking, the same answer: two servers lay one world.
    again = seed_planets.sites(constants, Planet.TERRA, COUNT, taken=[])
    assert [s.point for s in again] == [s.point for s in sites]
    #: A pinned point keeps its ground clear.
    pinned = sites[0].point
    clear = seed_planets.sites(constants, Planet.TERRA, COUNT, taken=[pinned])
    assert all(globe.distance_m(radius, s.point, pinned) >= spacing for s in clear)


def test_the_ways_make_one_piece_and_ford_only_where_they_must(constants: Constants) -> None:
    """Connected, and every crossing of water or a mountain is a bridge of the graph."""
    sites = seed_planets.sites(constants, Planet.TERRA, COUNT, taken=[])
    points = [site.point for site in sites]
    pairs = seed_planets.ways(constants, Planet.TERRA, points)
    assert pairs
    near: dict[int, set[int]] = {}
    for i, j in pairs:
        near.setdefault(i, set()).add(j)
        near.setdefault(j, set()).add(i)
    seen = {0}
    edge = [0]
    while edge:
        here = edge.pop()
        for other in near.get(here, ()):
            if other not in seen:
                seen.add(other)
                edge.append(other)
    assert len(seen) == len(points), "поверхность — одно целое"
    #: A crossing is kept only where the graph would fall apart without it.
    for i, j in pairs:
        if seed_planets._crosses(constants, Planet.TERRA, points[i], points[j]) is None:
            continue
        rest = [pair for pair in pairs if pair != (i, j)]
        reach = {i}
        edge = [i]
        while edge:
            here = edge.pop()
            for a, b in rest:
                other = b if a == here else a if b == here else None
                if other is not None and other not in reach:
                    reach.add(other)
                    edge.append(other)
        assert j not in reach, "брод есть только там, где без него не пройти"


def _hours_a_body_has(constants: Constants) -> float:
    return float(constants[R.BODY_STAMINA_MAX]) / float(constants[R.TRAVEL_STAMINA_PER_HOUR])


def _wild_hours(constants: Constants, metres: float) -> float:
    walk = float(constants[R.TRAVEL_WALK_SPEED_KMH]) * METRES_PER_KM
    return metres / walk * float(constants[R.ROAD_WILD_MULTIPLIER])


def test_every_way_is_within_a_bodys_strength(constants: Constants) -> None:
    """The review of the wave: nodes spread over the true-size sphere stood a
    month of walking apart. Inside the settled edge every way is hours."""
    sites = seed_planets.sites(constants, Planet.TERRA, COUNT, taken=[])
    points = [site.point for site in sites]
    radius = globe.radius_m(constants, Planet.TERRA)
    for i, j in seed_planets.ways(constants, Planet.TERRA, points):
        hours = _wild_hours(constants, globe.distance_m(radius, points[i], points[j]))
        assert hours <= _hours_a_body_has(constants), "ребро длиннее, чем хватает выносливости"


def test_two_regions_are_two_islands(constants: Constants) -> None:
    """Two seeded cities far apart get two settled edges, and no way between
    them: the tree fords a river, never an ocean."""
    radius = globe.radius_m(constants, Planet.TERRA)
    first = seed_planets._first_land(constants, Planet.TERRA)
    assert first is not None
    lat_max = constants[R.MAP_CITY_LAT_MAX]
    dry = [
        point
        for point in seed_planets._spiral(seed_planets.OVERSAMPLE**2)
        if abs(point[0]) <= lat_max and terrain.is_land(constants, Planet.TERRA, *point)
    ]
    second = max(dry, key=lambda point: globe.distance_m(radius, point, first))
    sites = seed_planets.sites(constants, Planet.TERRA, COUNT, taken=[], centres=[first, second])
    assert {site.region for site in sites} == {0, 1}, "оба края получили узлы"
    points = [site.point for site in sites]
    pairs = seed_planets.ways(
        constants, Planet.TERRA, points, regions=[site.region for site in sites]
    )
    for i, j in pairs:
        assert sites[i].region == sites[j].region, "ребро между краями"


async def test_the_seed_lays_the_count_on_the_relief(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    sphere, nodes = await _laid(session, constants, catalog)
    assert len(nodes) == COUNT
    for node in nodes:
        point = places.geo_of(node)
        assert point is not None and node.parent_id == sphere.id
        marks = terrain.marks_at(constants, Planet.TERRA, *point)
        #: The map's river is always water; a stream may fall out beside it (D-126).
        assert node.properties[world.WATER] in (marks[world.WATER], world.RIVER)
        assert (
            node.properties["temperature"] == terrain.climate_at(constants, Planet.TERRA, *point)[0]
        )
        assert node.properties["wild"] is True
    edges = (await session.execute(select(func.count()).select_from(Edge))).scalar_one()
    assert edges >= COUNT - 1, "рёбер не меньше, чем нужно одному куску"
    for edge in (await session.execute(select(Edge))).scalars():
        assert edge.base_seconds > 0
    #: Laid once: the second run finds the first node and lays nothing.
    again = await seed_planets.lay(
        session, constants, catalog, Planet.TERRA, sphere=sphere, count=COUNT
    )
    assert again == []


async def _city_at(session: AsyncSession, sphere: Node, key: str, point: globe.Geo) -> Node:
    """A seeded city's stand-in: a delegate pinned at `point` with one node of its own."""
    city = await world.create_node(
        session,
        key,
        key.title(),
        area_m2=1,
        planet=sphere.planet,
        parent=sphere,
        properties={places.PLACE: {places.PLACE_LAT: point[0], places.PLACE_LON: point[1]}},
    )
    await world.create_node(
        session, f"{key}.core", "Core", area_m2=100, planet=sphere.planet, parent=city
    )
    await session.flush()
    return city


async def test_the_mountains_bear_veins_more_often(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The share of veins on the plain is the vault's; in the mountains it is
    `terrain.mountain_vein_k` times that, over a count large enough to show.

    Two settled edges, one round a city in the mountains and one on the plain:
    a region is kilometres wide and a relief cell is degrees, so one region
    alone is all mountain or none.
    """
    sphere = await _sphere(session)
    field = terrain.field_of(constants, Planet.TERRA)
    lat_max = constants[R.MAP_CITY_LAT_MAX]
    dry = [
        point
        for point in seed_planets._spiral(seed_planets.OVERSAMPLE**2)
        if abs(point[0]) <= lat_max and terrain.is_land(constants, Planet.TERRA, *point)
    ]
    highland = next(point for point in dry if field.is_mountain(*point))
    lowland = next(point for point in dry if not field.is_mountain(*point))
    await _city_at(session, sphere, "terra.highcity", highland)
    await _city_at(session, sphere, "terra.lowcity", lowland)
    nodes = await seed_planets.lay(
        session, constants, catalog, Planet.TERRA, sphere=sphere, count=COUNT * 3
    )
    veins = {node_id for (node_id,) in await session.execute(select(Vein.node_id))}
    high = [node for node in nodes if node.properties.get(terrain.MOUNTAIN)]
    low = [node for node in nodes if not node.properties.get(terrain.MOUNTAIN)]
    assert high and low, "и горы, и равнина есть"
    share_high = sum(node.id in veins for node in high) / len(high)
    share_low = sum(node.id in veins for node in low) / len(low)
    assert share_high >= share_low
    for node in high:
        assert node.properties["stones"] is True and node.properties["woods"] is False


async def test_a_city_of_the_forerunners_is_laid_open_frozen_and_dark(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    sphere = await _sphere(session, Planet.AURORA)
    nodes = await seed_planets.lay(
        session, constants, catalog, Planet.AURORA, sphere=sphere, count=4, lost_cities=1
    )
    ports = [node for node in nodes if node.key.endswith(".port")]
    assert len(ports) == 1
    port = ports[0]
    city = await ruins.city_of(session, port)
    assert city is not None and places.geo_of(city) is not None
    assert ruins.exhausted(constants, city), "все помещения открыты с рождения"
    rooms = (
        await session.execute(
            select(func.count()).select_from(Node).where(Node.key.like(f"{city.key}.room.%"))
        )
    ).scalar_one()
    assert rooms == int(constants[R.RUINS_CITY_ROOMS])
    with pytest.raises(ruins.NotRuins):
        await ruins.open_room(session, constants, random.Random(1), port)


async def test_nothing_is_laid_where_nothing_is_asked(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    sphere = await _sphere(session, Planet.AQUATICA)
    assert (
        await seed_planets.lay(session, constants, catalog, Planet.AQUATICA, sphere=sphere, count=0)
        == []
    )
    assert uuid.UUID(str(sphere.id))
