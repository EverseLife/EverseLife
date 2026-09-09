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

import json
import math
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
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


def test_the_local_relief_is_read_from_the_tile_the_client_draws(constants: Constants) -> None:
    """A tile's lattice points are the field's own heights, and between them
    the field reads bilinearly (D-323): the ground drawn and the ground found
    are one set of numbers."""
    field = terrain.field_of(constants, Planet.TERRA)
    assert field.detail_amplitude > 0, "Терра с местным рельефом"
    row, col = relief.tile_of(41.0, 24.0)
    tile = terrain.tile(constants, Planet.TERRA, row, col)
    assert tile is not None and tile["n"] == relief.TILE_N
    #: The wire carries the noise alone (D-225): the client has the grid.
    assert "grid" not in tile
    assert len(tile["local"]) == relief.TILE_N + 1 and len(tile["local"][0]) == relief.TILE_N + 1
    step = tile["step"]
    heights, _ = field.tile(row, col)
    for i, j in ((0, 0), (17, 63), (relief.TILE_N, relief.TILE_N)):
        lat = min(90.0, tile["lat0"] + i * step)
        lon = tile["lon0"] + j * step
        assert abs(float(heights[i][j]) - field.height(lat, lon)) < 1e-4, (i, j)
        assert abs(tile["local"][i][j] - field.local(lat, lon)) < 1e-4, (i, j)
    #: Written once: the same bytes come back, and none off the planet.
    first = terrain.tile_json(constants, Planet.TERRA, row, col)
    assert first is not None and first is terrain.tile_json(constants, Planet.TERRA, row, col)
    assert terrain.tile_json(constants, Planet.TERRA, 99, 0) is None
    #: The field keeps a neighbourhood of tiles, not the planet.
    for r in range(relief.tile_counts()[0]):
        for c in range(0, relief.tile_counts()[1], 3):
            field.tile(r, c)
    assert len(field.tiles) <= relief.TILE_KEEP
    #: Halfway between two lattice points the height is their mean.
    a = field.height(tile["lat0"] + 5 * step, tile["lon0"] + 5 * step)
    b = field.height(tile["lat0"] + 5 * step, tile["lon0"] + 6 * step)
    mid = field.height(tile["lat0"] + 5 * step, tile["lon0"] + 5.5 * step)
    assert abs(mid - (a + b) / 2) < 1e-9
    #: Off the planet there is no tile.
    assert terrain.tile(constants, Planet.TERRA, 18, 0) is None
    assert terrain.tile(constants, Planet.TERRA, 0, 36) is None


def test_peaks_are_mountains_and_basins_lakes_at_the_vault_shares(constants: Constants) -> None:
    """The local relief does not move a coast; on land its highest share is
    mountain wherever it stands and its lowest share holds water on a planet
    with a sea -- and nowhere else does the land dip under the sea."""
    field = terrain.field_of(constants, Planet.TERRA)
    rng = np.random.default_rng(5)
    land: list[tuple[float, float]] = []
    while len(land) < 2000:
        lat = math.degrees(math.asin(rng.uniform(-1, 1)))
        lon = rng.uniform(-180.0, 180.0)
        #: The coast is the grid's word, whatever the local noise says.
        assert field.is_sea(lat, lon) == (field.coarse(lat, lon) < field.sea_level)
        if field.is_sea(lat, lon):
            #: No shoal rises out of the sea -- away from the shore, where
            #: the tile's lattice reads the coast a step from the grid's.
            if field.coarse(lat, lon) < field.sea_level - 0.02:
                assert field.height(lat, lon) < field.sea_level, "в море нет мелей из местного шума"
            continue
        land.append((lat, lon))
    lakes = sum(field.is_lake(*p) for p in land) / len(land)
    mountains = sum(field.is_mountain(*p) for p in land) / len(land)
    basin = float(constants[R.TERRAIN_BASIN_SHARE])
    peak = float(constants[R.TERRAIN_PEAK_SHARE])
    assert basin * 0.4 <= lakes <= basin * 2.0, lakes
    assert mountains >= peak * 0.8, mountains
    #: Dry land is dry: the local noise never sinks the land under the sea --
    #: away from the coast, where the tile's lattice and the grid's read the
    #: shore a step apart.
    inland = [p for p in land if field.coarse(*p) >= field.sea_level + 0.02]
    assert inland and all(field.height(*p) >= field.sea_level for p in inland)
    #: A planet without a sea has basins but no lakes.
    dry = terrain.field_of(constants, Planet.PYROXIS)
    assert not dry.wet
    assert not any(dry.is_lake(*p) for p in land[:300])
    #: The planet's ranges are the grid's word: a peak of the noise adds a
    #: mountain, a dip of it never takes one away.
    ranges = [p for p in land if field.coarse(*p) >= field.mountain_level]
    assert ranges and all(field.is_mountain(*p) for p in ranges)
    #: A lake waters the node on its bank as a river does. On the **bank**:
    #: the reach is read on eight rays at three distances (`relief.around`),
    #: and a lake is a thin sliver of a basin -- the one found here runs
    #: 0.04 deg across against a reach of 0.58 -- so a node well within the
    #: reach can still fall between two rings of rays and read dry. That is
    #: OQ-152 and not this file's to settle; what is pinned here is the bank,
    #: which no ring can miss.
    lake = next(p for p in land if field.is_lake(*p))
    step = 0.01
    beside = lake
    while field.is_water(*beside) and abs(beside[0] - lake[0]) < 1.0:
        beside = (beside[0] + step, beside[1])
    if not field.is_water(*beside):
        assert terrain.marks_at(constants, Planet.TERRA, *beside)[world.WATER] in (
            world.LAKE,
            world.RIVER,
        )


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


def test_the_sketch_tool_builds_the_field_the_numbers_ask_for(
    constants: Constants, tmp_path
) -> None:
    """`tools/sketch.py`: the same field, with numbers tried over the build.

    The vault's editor draws a planet while its numbers are being turned, and
    the shape those numbers make is **this** arithmetic. The tool is the door
    it asks through, so that the world never has two shapes -- the one a tool
    shows and the one the game builds. What is pinned here is the door: a
    value put over the build changes the field, and nothing is written.
    """
    build = tmp_path / "build"
    build.mkdir()
    #: The build the engine itself is running on, copied so the tool has one
    #: to read: the test must not depend on where the vault is checked out.
    raw = json.loads(Path(constants.source).read_text(encoding="utf-8"))
    (build / "constants.json").write_text(json.dumps(raw), encoding="utf-8")

    def run(*extra: str) -> dict:
        done = subprocess.run(
            [sys.executable, "tools/sketch.py", "--planet", "terra", "--build", str(build), *extra],
            capture_output=True,
            check=False,
            cwd=Path(__file__).resolve().parent.parent,
        )
        assert done.returncode == 0, done.stderr.decode("utf-8", errors="replace")
        return json.loads(done.stdout.decode("utf-8"))

    plain = run()
    assert plain["rows"] == relief.GRID_ROWS and plain["cols"] == relief.GRID_COLS
    turned = run("--set", "terrain.seed=17")
    assert turned["grid"] != plain["grid"], "другое зерно — другой мир"
    #: And the file the tool read is exactly as it was: a preview writes nothing.
    assert json.loads((build / "constants.json").read_text(encoding="utf-8")) == raw


def test_the_field_has_metres(constants: Constants) -> None:
    """A height in metres is the field's share of the rise times the vault's
    rise (landscape plan, wave 1): the sea reads zero, the highest land reads
    the whole `terrain.relief_m`, and nothing stands above it."""
    rise = float(constants[R.TERRAIN_RELIEF_M])
    assert rise > 0
    field = terrain.field_of(constants, Planet.TERRA)
    rows, cols = field.grid.shape
    highest = 0.0
    seen_sea = False
    for row in range(0, rows, 3):
        for col in range(0, cols, 3):
            lat, lon = field.centre(row, col)
            height = terrain.height_m(constants, Planet.TERRA, lat, lon)
            assert 0.0 <= height <= rise
            if field.is_water(lat, lon):
                seen_sea = True
                assert height == 0.0, "вода стоит на уровне моря"
            else:
                assert height == pytest.approx(field.relief(lat, lon) * rise)
            highest = max(highest, height)
    assert seen_sea and highest > rise / 2, "суша поднимается к своему размаху"
