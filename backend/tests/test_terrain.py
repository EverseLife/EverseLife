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
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src import field as fields
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


def test_a_tile_is_the_field_read_on_the_client_lattice(constants: Constants) -> None:
    """A tile's lattice points are the field's own heights (plan wave 2,
    D-323): the ground drawn and the ground found are one set of numbers,
    and a lake in it reads under the sea's zero so the client cuts water."""
    field = terrain.field_of(constants, Planet.TERRA)
    row, col = relief.tile_of(*next(p for p in seed_points() if not field.is_water(*p)))
    tile = terrain.tile(constants, Planet.TERRA, row, col)
    assert tile is not None and tile["n"] == relief.TILE_N
    #: The wire carries the tile alone (D-225): the client has the grid.
    assert "grid" not in tile
    assert len(tile["local"]) == relief.TILE_N + 1 and len(tile["local"][0]) == relief.TILE_N + 1
    step = tile["step"]
    for i, j in ((0, 0), (17, 63), (relief.TILE_N, relief.TILE_N)):
        lat = min(90.0, tile["lat0"] + i * step)
        lon = tile["lon0"] + j * step
        expected = field.height_at(lat, lon)
        if field.is_lake(lat, lon):
            expected = min(expected, fields.LAKE_SINK)
        assert abs(tile["local"][i][j] - expected) < 1e-4, (i, j)
    #: Written once: the same bytes come back, and none off the planet.
    first = terrain.tile_json(constants, Planet.TERRA, row, col)
    assert first is not None and first is terrain.tile_json(constants, Planet.TERRA, row, col)
    assert terrain.tile_json(constants, Planet.TERRA, 99, 0) is None
    assert terrain.tile(constants, Planet.TERRA, 18, 0) is None
    assert terrain.tile(constants, Planet.TERRA, 0, 36) is None
    #: Halfway between two cells the height is their mean.
    lat, lon = field.centre(field.rows // 2, field.cols // 2)
    dlon = 360.0 / field.cols
    a = field.height_at(lat, lon)
    b = field.height_at(lat, lon + dlon)
    assert abs(field.height_at(lat, lon + dlon / 2) - (a + b) / 2) < 1e-9


def seed_points() -> list[tuple[float, float]]:
    return [(lat, lon) for lat in range(-50, 51, 2) for lon in range(-180, 180, 2)]


def test_water_mountains_and_lakes_are_the_rasters_word(constants: Constants) -> None:
    """The sea is the water raster's cell, a mountain the height over the
    line that leaves `terrain.mountain_share` of the land above it, and a
    lake waters the node on its bank as a river does."""
    field = terrain.field_of(constants, Planet.TERRA)
    rng = np.random.default_rng(5)
    land: list[tuple[float, float]] = []
    while len(land) < 2000:
        lat = math.degrees(math.asin(rng.uniform(-1, 1)))
        lon = rng.uniform(-180.0, 180.0)
        assert field.is_sea(lat, lon) == (field.water[field.cell(lat, lon)] == fields.SEA)
        if field.is_sea(lat, lon):
            assert field.height_at(lat, lon) <= 0.05, "море стоит не выше кромки"
            continue
        land.append((lat, lon))
    mountains = sum(field.is_mountain(*p) for p in land) / len(land)
    share = float(constants[R.TERRAIN_MOUNTAIN_SHARE])
    assert share * 0.5 <= mountains <= share * 1.6, mountains
    assert all(field.relief(*p) >= 0.0 for p in land)
    #: A planet without a sea has no lake to water a node.
    dry = terrain.field_of(constants, Planet.PYROXIS)
    assert not dry.is_sea(0.0, 0.0)
    lakes = [p for p in land if field.is_lake(*p)]
    assert lakes, "на Терре есть озёра"
    lake = lakes[0]
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
    rivers = np.argwhere(field.water == fields.RIVER)
    assert len(rivers), "у Терры есть реки"
    on_river = field.centre(*(int(v) for v in rivers[len(rivers) // 2]))
    assert terrain.marks_at(constants, Planet.TERRA, *on_river)[world.WATER] == world.RIVER
    reach = terrain.river_reach_deg(constants, Planet.TERRA, 0.0)
    far = next(
        (lat, lon)
        for lat in range(-60, 61, 3)
        for lon in range(-180, 180, 3)
        if not field.is_water(lat, lon) and field.river_distance_deg(lat, lon) > reach * 4
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
    assert max(lows) <= warm + 1 and min(highs) >= cold - constants[R.TERRAIN_LAPSE_C] - 10
    assert sum(lows) / len(lows) > sum(highs) / len(highs), "у экватора теплее, чем у полюса"
    #: The same latitude, a peak against the shore: the peak is colder.
    peak = max(
        ((lat, lon) for lat in range(-30, 31, 2) for lon in range(-180, 180, 2)),
        key=lambda p: field.height_at(*p),
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
    field = terrain.field_of(constants, Planet.TERRA)
    sketch = terrain.sketch(constants, Planet.TERRA)
    rows, cols = field.grid.shape
    assert sketch["rows"] == rows and len(sketch["grid"]) == rows
    assert len(sketch["grid"][0]) == cols and rows <= fields.SKETCH_ROWS
    assert sketch["sea_level"] == 0.0 < sketch["mountain_level"] < 1.0
    assert sketch["basin_level"] == 0.0 and sketch["peak_level"] == sketch["mountain_level"]
    assert sketch["lakes"] and all(len(cell) == 2 for cell in sketch["lakes"])
    warmth = sketch["warmth"]
    assert len(warmth) == rows
    middle = rows // 2
    assert warmth[0] < warmth[middle] > warmth[-1], "экватор теплее полюсов"


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
    #: And the field beside it: the tool reads the planet from the build's
    #: `field/` as the engine does (plan wave 2).
    shutil.copytree(Path(constants.source).parent / "field", build / "field")

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
    assert plain["rows"] > 0 and plain["cols"] == 2 * plain["rows"]
    turned = run("--set", "terrain.mountain_share=0.5")
    assert turned["mountain_level"] != plain["mountain_level"], "другая доля гор — другая линия"
    #: And the file the tool read is exactly as it was: a preview writes nothing.
    assert json.loads((build / "constants.json").read_text(encoding="utf-8")) == raw


def test_the_field_has_metres(constants: Constants) -> None:
    """A height in metres (landscape plan, wave 1): the sea reads zero, a
    lake reads the land under it like the climate does, and up the same
    slope the metres rise with the share -- on a field built for the test,
    so the vault's seed does not decide what passes."""
    rise = float(constants[R.TERRAIN_RELIEF_M])
    assert rise > 0
    field = terrain.field_of(constants, Planet.TERRA)
    rows, cols = field.rows, field.cols
    seas = np.argwhere(field.water == fields.SEA)
    lakes = np.argwhere(field.water == fields.LAKE)
    sea = field.centre(*(int(v) for v in seas[0])) if len(seas) else None
    lake = field.centre(*(int(v) for v in lakes[0])) if len(lakes) else None
    assert sea is not None, "на Терре есть море"
    assert terrain.height_m(constants, Planet.TERRA, *sea) == 0.0, "море стоит на нуле"
    if lake is not None:
        assert terrain.height_m(constants, Planet.TERRA, *lake) == pytest.approx(
            field.relief(*lake) * rise
        ), "озеро стоит на своей земле, как в климате"
        assert terrain.height_m(constants, Planet.TERRA, *lake) > 0.0
    #: Monotone: the reading in metres orders the land as the share does.
    land = [
        field.centre(row, col)
        for row in range(0, rows, 5)
        for col in range(0, cols, 5)
        if not field.is_water(*field.centre(row, col))
    ]
    assert land, "на Терре есть суша"
    heights = [terrain.height_m(constants, Planet.TERRA, *point) for point in land]
    shares = [field.relief(*point) for point in land]
    assert all(0.0 <= h <= rise for h in heights)
    for k in range(1, len(land)):
        assert (heights[k - 1] <= heights[k]) == (shares[k - 1] <= shares[k])
