# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A planet is made of something, and it is the same something for everybody (D-319).

The field -- seas, mountains, rivers, lakes, climate -- is the vault's
build, read at a point, never rolled (landscape plan, wave 2). What is
checked here is the whole of that:

* a tile is the field's own heights on the client's lattice, and a sketch
  its coarser grid with the sea at zero;
* water, mountains and lakes are the rasters' word, and a lake waters the
  node on its bank;
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


def test_the_noise_has_no_seam_and_is_the_same_everywhere() -> None:
    """The ground's marks still read the noise on the sphere: one reading for
    one seed on every machine, and none at the antimeridian."""
    assert relief.noise_at(7, 10.0, 179.999) == pytest.approx(
        relief.noise_at(7, 10.0, -179.999), abs=1e-3
    )
    assert relief.noise_at(7, 10.0, 10.0) == relief.noise_at(7, 10.0, 10.0)
    assert relief.noise_at(7, 10.0, 10.0) != relief.noise_at(8, 10.0, 10.0)
    assert 0.0 <= relief.noise_at(7, 10.0, 10.0) <= 1.0


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
    #: The field's temperature may fall under the cold end by the depth of the
    #: continent, the local weather and the whole rise's lapse, and no further.
    floor = (
        cold
        - constants[R.TERRAIN_CONTINENTAL_C]
        - constants[R.TERRAIN_CLIMATE_NOISE_C]
        - constants[R.TERRAIN_LAPSE_C]
    )
    assert max(lows) <= warm and min(highs) >= floor
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
    #: The coast is the raster's word, not the block's average: a sketch
    #: cell is over the sea's zero exactly where most of its cells are land,
    #: so the client's coarse "sea" and the server's land check agree.
    factor = field.rows // rows
    land = (field.water[: rows * factor, : cols * factor] != fields.SEA).reshape(
        rows, factor, cols, factor
    )
    majority = land.mean(axis=(1, 3)) > 0.5
    assert np.array_equal(field.grid > 0.0, majority), "знак клетки эскиза — большинство её суши"
    assert (sketch["warmth"][rows // 2] > sketch["warmth"][0]) and len(sketch["warmth"]) == rows
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


# --- the picture's rasters (landscape plan wave 5) -----------------------------


def test_the_biome_raster_is_the_classifier_at_every_cell_centre(constants: Constants) -> None:
    """The shader draws by the raster and the find is sorted by the point:
    one rule, two readings, held together here (plan §9.1)."""
    from src.engine import biome

    field = terrain.field_of(constants, Planet.TERRA)
    raster = biome.raster(constants, Planet.TERRA)
    names = biome.codes(constants)
    assert raster.shape == field.height.shape and raster.dtype == np.uint8
    rows, cols = raster.shape
    for row in range(0, rows, 23):
        for col in range(0, cols, 41):
            point = field.centre(row, col)
            word = biome.classify(constants, Planet.TERRA, *point)
            code = int(raster[row, col])
            assert (word is None and code == biome.NONE) or (
                code < len(names) and names[code] == word
            ), f"клетка {row},{col}: растр говорит {code}, классификатор {word}"
    assert biome.raster(constants, Planet.AURORA).max() < len(names)
    icy = np.unique(biome.raster(constants, Planet.AURORA))
    assert set(icy.tolist()) <= {names.index(biome.ICE), biome.NONE}, "Аврора — один лёд"


def test_the_rasters_are_the_field_thinned_and_named(constants: Constants) -> None:
    from src.engine import rasters

    field = terrain.field_of(constants, Planet.TERRA)
    passport = terrain.sketch(constants, Planet.TERRA)["raster"]
    stride = terrain.raster_stride(field.rows)
    assert passport["rows"] == len(range(0, field.rows, stride)) <= terrain.RASTER_ROWS_MAX
    assert passport["cols"] == len(range(0, field.cols, stride))
    assert passport["step_m"] == field.step_m * stride and passport["relief_m"] == field.relief_m
    assert passport["forms"] == list(field.forms) and passport["biomes"] == list(
        constants[R.BIOME_NAMES]
    )
    n = passport["rows"] * passport["cols"]
    height = np.frombuffer(rasters.raster_bytes(constants, Planet.TERRA, "height"), dtype="<i2")
    assert height.size == n
    assert height.min() < 0 < height.max() <= field.relief_m, "море ниже нуля, суша до размаха"
    sea = field.height[::stride, ::stride].reshape(-1) < 0
    assert (height[sea] < 0).all(), "the sea stays under zero, shallow cells too"
    land = ~sea
    assert (height[land] >= 0).all()
    for kind in ("biome", "form", "water"):
        got = np.frombuffer(rasters.raster_bytes(constants, Planet.TERRA, kind), dtype=np.uint8)
        assert got.size == n
    water = np.frombuffer(rasters.raster_bytes(constants, Planet.TERRA, "water"), dtype=np.uint8)
    assert passport["water"][fields.RIVER] == "river" and (water == fields.RIVER).any()
    assert rasters.raster_bytes(constants, Planet.TERRA, "rivers") is None
    #: Written once: the second ask is the same bytes object.
    assert rasters.raster_bytes(constants, Planet.TERRA, "height") is rasters.raster_bytes(
        constants, Planet.TERRA, "height"
    )
