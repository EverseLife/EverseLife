# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A planet is made of something, and it is the same something for everybody (D-319).

The relief -- seas, mountains, rivers -- is built from the vault's seed and
read at a point, never rolled. What is checked here is the whole of that:

* the same seed builds the same field, and a different seed a different one;
* the sea is the share the vault asks for, and a river ends in water;
* a node beside a river carries river water, and nowhere else does;
* the climate is warm at the equator, cold at the pole and colder uphill;
* the planet turns: noon comes to the east first, by the longitude's share.
"""

from __future__ import annotations

import math
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src import relief
from src.constants import Constants
from src.constants import registry as R
from src.engine import climate, places, terrain, world
from src.models.world import Layer, Planet

SEED = 7


def test_the_same_seed_builds_the_same_world() -> None:
    """Two servers replaying one vault lay one relief; a different seed lays another."""
    one = relief.build(SEED, sea_share=0.6, mountain_share=0.15, rivers=8)
    twin = relief.build(SEED, sea_share=0.6, mountain_share=0.15, rivers=8)
    other = relief.build(SEED + 1, sea_share=0.6, mountain_share=0.15, rivers=8)
    assert (one.grid == twin.grid).all() and one.rivers == twin.rivers
    assert not (one.grid == other.grid).all()
    assert one.grid.min() >= 0.0 and one.grid.max() <= 1.0


def test_the_sea_is_the_share_asked_for_and_the_field_has_no_seam() -> None:
    """The sea level is the quantile of the grid; the noise wraps the antimeridian."""
    field = relief.build(SEED, sea_share=0.6, mountain_share=0.15, rivers=0)
    assert field.land_share() == pytest.approx(0.4, abs=0.02)
    assert relief.noise_at(SEED, 10.0, 179.999) == pytest.approx(
        relief.noise_at(SEED, 10.0, -179.999), abs=1e-3
    )
    dry = relief.build(SEED, sea_share=0.0, mountain_share=0.15, rivers=0)
    assert dry.land_share() == 1.0 and not dry.is_sea(0.0, 0.0)
    assert math.isinf(dry.river_distance_deg(0.0, 0.0)), "без рек расстояние до реки бесконечно"


def test_a_river_runs_downhill_and_ends_in_water() -> None:
    """Every river starts on land, drops with every step and stops at the sea or in a lake."""
    field = relief.build(SEED, sea_share=0.6, mountain_share=0.15, rivers=12)
    assert 0 < len(field.rivers) <= 12
    for river in field.rivers:
        assert not field.is_sea(*river[0]), "исток на суше"
        heights = [field.grid[field.cell(*point)] for point in river]
        assert all(later < earlier for earlier, later in zip(heights, heights[1:], strict=False))
        end = river[-1]
        assert field.is_sea(*end) or field.cell(*end) in field.lakes, "река кончается водой"


def test_river_sources_keep_apart_by_arc_even_at_the_pole() -> None:
    """Two rivers do not rise within a few degrees of each other -- measured on
    the sphere, where a polar row of cells is one point, not a hundred and
    eighty sources."""
    for seed in range(SEED, SEED + 6):
        field = relief.build(seed, sea_share=0.6, mountain_share=0.15, rivers=24)
        sources = [river[0] for river in field.rivers]
        for i, a in enumerate(sources):
            for b in sources[i + 1 :]:
                assert relief._arc_deg(a, b) >= relief.SOURCE_SPACING_DEG, (seed, a, b)
        #: The complaint itself: a polar cap is a few rivers, not a fan of
        #: twenty -- the last five degrees round a pole hold six sources
        #: five degrees apart at most.
        for cap in (1, -1):
            polar = [a for a in sources if cap * a[0] >= 85.0]
            assert len(polar) <= 6, (seed, cap, polar)


def test_the_river_mark_is_a_fact_of_the_map(constants: Constants) -> None:
    """Within the reach of a river a node has river water; far from every river it has none."""
    field = terrain.field_of(constants, Planet.TERRA)
    assert field.rivers, "у Терры есть реки"
    on_river = field.rivers[0][len(field.rivers[0]) // 2]
    assert terrain.marks_at(constants, Planet.TERRA, *on_river)[world.WATER] == world.RIVER
    reach = terrain.river_reach_deg(constants, Planet.TERRA, 0.0)
    #: A point the reach and then some away from every river: walk the grid
    #: until one turns up, there is always one on a planet three-fifths sea.
    far = next(
        (lat, lon)
        for lat in range(-60, 61, 3)
        for lon in range(-180, 180, 3)
        if field.river_distance_deg(lat, lon) > reach * 4
    )
    assert terrain.marks_at(constants, Planet.TERRA, *far)[world.WATER] == world.NO_WATER


def test_the_climate_follows_the_latitude_and_the_height(constants: Constants) -> None:
    """Warm at the equator, cold at the pole, colder again uphill (D-261, D-319)."""
    field = terrain.field_of(constants, Planet.TERRA)
    warm, cold = constants[R.SITE_TEMP_RANGE].max, constants[R.SITE_TEMP_RANGE].min
    lows = [
        terrain.climate_at(constants, Planet.TERRA, 0.0, lon)[0] for lon in range(-180, 180, 20)
    ]
    highs = [
        terrain.climate_at(constants, Planet.TERRA, 75.0, lon)[0] for lon in range(-180, 180, 20)
    ]
    assert max(lows) <= warm and min(highs) >= cold - constants[R.TERRAIN_LAPSE_C]
    assert sum(lows) / len(lows) > sum(highs) / len(highs), "у экватора теплее, чем у полюса"
    #: The same latitude, a peak against the shore: the peak is colder.
    peak = max(
        ((lat, lon) for lat in range(-30, 31, 2) for lon in range(-180, 180, 2)),
        key=lambda p: field.height(*p),
    )
    shore = min(((peak[0], lon) for lon in range(-180, 180, 2)), key=lambda p: field.relief(*p))
    assert (
        terrain.climate_at(constants, Planet.TERRA, *peak)[0]
        < terrain.climate_at(constants, Planet.TERRA, *shore)[0]
    )
    rain = constants[R.SITE_RAIN_RANGE]
    assert rain.min <= terrain.climate_at(constants, Planet.TERRA, 10.0, 10.0)[1] <= rain.max


def test_no_node_stands_in_the_sea_or_past_the_last_latitude(constants: Constants) -> None:
    field = terrain.field_of(constants, Planet.TERRA)
    wet = next(
        (lat, lon)
        for lat in range(-50, 51, 2)
        for lon in range(-180, 180, 2)
        if field.is_sea(lat, lon)
    )
    dry = next(
        (lat, lon)
        for lat in range(-50, 51, 2)
        for lon in range(-180, 180, 2)
        if not field.is_water(lat, lon)
    )
    assert not terrain.is_land(constants, Planet.TERRA, *wet)
    assert terrain.is_land(constants, Planet.TERRA, *dry)
    assert not terrain.is_land(constants, Planet.TERRA, constants[R.MAP_CITY_LAT_MAX] + 1, 0.0)


def test_the_sketch_is_the_field_the_client_draws(constants: Constants) -> None:
    sketch = terrain.sketch(constants, Planet.TERRA)
    assert sketch["rows"] == relief.GRID_ROWS and len(sketch["grid"]) == relief.GRID_ROWS
    assert len(sketch["grid"][0]) == relief.GRID_COLS
    assert sketch["rivers"] and all(
        len(point) == 2 for river in sketch["rivers"] for point in river
    )
    assert 0.0 < sketch["sea_level"] < sketch["mountain_level"] < 1.0
    warmth = sketch["warmth"]
    assert len(warmth) == relief.GRID_ROWS
    middle = relief.GRID_ROWS // 2
    assert warmth[0] < warmth[middle] > warmth[-1], "экватор теплее полюсов"
    assert warmth[middle] == round(terrain.by_latitude(constants, 1.0))


async def test_noon_comes_to_the_east_first(session: AsyncSession, constants: Constants) -> None:
    """The planet turns (D-319): a node a quarter turn east is a quarter of a day ahead."""
    terra = await world.create_node(session, "terra.turn", "Терра", area_m2=1, layer=Layer.SPACE)
    origin = await world.epoch(session)
    assert origin is not None
    day = climate.day_hours_of(constants, Planet.TERRA)
    east = await world.create_node(
        session,
        "terra.turn.east",
        "Восток",
        area_m2=100,
        parent=terra,
        properties={
            "temperature": 20,
            places.PLACE: {places.PLACE_LAT: 0.0, places.PLACE_LON: 90.0},
        },
    )
    west = await world.create_node(
        session,
        "terra.turn.west",
        "Запад",
        area_m2=100,
        parent=terra,
        properties={
            "temperature": 20,
            places.PLACE: {places.PLACE_LAT: 0.0, places.PLACE_LON: -90.0},
        },
    )
    #: The meridian's noon: the east is already at dusk, the west at dawn.
    noon = origin + timedelta(hours=day / 2)
    assert climate.day_phase(constants, Planet.TERRA, origin, noon) == pytest.approx(0.5)
    assert climate.day_phase(
        constants, Planet.TERRA, origin, noon, longitude=90.0
    ) == pytest.approx(0.75)
    assert climate.day_phase(
        constants, Planet.TERRA, origin, noon, longitude=-90.0
    ) == pytest.approx(0.25)
    swing = climate.swing_of(constants, Planet.TERRA)
    #: A quarter turn east, the meridian's noon is the east's dusk: the mean.
    assert climate.temperature_now(constants, east, origin, noon) == pytest.approx(20.0, abs=1e-6)
    assert climate.temperature_now(constants, west, origin, noon) == pytest.approx(20.0, abs=1e-6)
    #: And six hours on, the east has cooled past the mean while the west warms.
    later = noon + timedelta(hours=day / 8)
    assert climate.temperature_now(constants, east, origin, later) < 20.0
    assert climate.temperature_now(constants, west, origin, later) > 20.0
    assert swing > 0
