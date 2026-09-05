# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The whole surface of a planet is laid at the world's birth (D-319).

Nothing is found any more: the wild nodes, the ways between them, the veins
and the frozen cities exist from the first day. What is checked here:

* the count the vault asks for is laid, on land, apart, and clear of what
  the layout pinned;
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


async def test_the_seed_lays_the_count_on_the_relief(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    sphere, nodes = await _laid(session, constants, catalog)
    assert len(nodes) == COUNT
    for node in nodes:
        point = places.geo_of(node)
        assert point is not None and node.parent_id == sphere.id
        marks = terrain.marks_at(constants, Planet.TERRA, *point)
        assert node.properties[world.WATER] == marks[world.WATER]
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


async def test_the_mountains_bear_veins_more_often(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The share of veins on the plain is the vault's; in the mountains it is
    `terrain.mountain_vein_k` times that, over a count large enough to show."""
    sphere = await _sphere(session)
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
