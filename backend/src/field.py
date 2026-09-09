# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A planet's field, read from the vault's build (landscape plan, wave 2).

The vault makes the field -- plates, rock, rivers by flow, erosion, climate,
landforms -- and lays it in `build/field/<planet>.npz` beside a passport
(`<planet>.json`) that names its parameters and their digest. The game
reads it; nothing here computes a planet. That is the plan's §4.8: erosion
on floating point is not the same to the last bit on two machines, so the
file is the one truth about the shape of the world, and the server would
rather refuse to start than draw a different one.

The field answers the questions the engine already asked of the noise
field (`relief.Field`): what is under a point (sea, lake, land, mountain),
how high, how far the nearest river, whether a straight way crosses one.
It also carries what the noise never had -- the landform, the rock, the
temperature and rain of a point -- for the waves that read them.

Rasters are read with the cell's own value where the thing is categorical
(water, form) and between the four nearest cells where it is a quantity
(height): a coast is a line through the cells, not a staircase of them.
The sketch the client draws is a coarser grid cut from the same height, and
a tile is a window of it sampled on the client's lattice.
"""

from __future__ import annotations

import functools
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src import relief
from src.constants import Constants
from src.constants import registry as R
from src.models.world import Planet

#: The water raster's codes, as the vault's pipeline writes them.
LAND, SEA, LAKE, RIVER = 0, 1, 2, 3
#: The sketch is at most this many rows: a page load must not parse a
#: planet's every cell, and the tiles refine the ground close up.
SKETCH_ROWS = 128
#: A lake in a tile reads this far under the sea's level: the client cuts
#: water where the tile is at or under zero.
LAKE_SINK = -0.01
#: A byte-scaled share: 255 is one.
BYTE = 255.0
#: How far off zero a sketch cell is held on the side its land majority says,
#: when its mean height would put it on the other: a coast cell reads as the
#: raster does, not as its average.
SKETCH_SHORE = 1e-3


class FieldMissing(RuntimeError):
    """The vault's build carries no field for this planet."""


class FieldStale(RuntimeError):
    """The field was built under other numbers than the constants say."""


#: How closely a passport's number must match the registry's: shares and
#: degrees to a thousandth, metres to the metre.
PASSPORT_TOLERANCE = 1e-3


@dataclass(frozen=True)
class Field:
    """A planet's field as the engine reads it. Heights are shares of the
    land's rise (`terrain.relief_m`): the sea negative, the highest summit one."""

    planet: str
    seed: int
    step_m: float
    radius_m: float
    relief_m: float
    #: The rasters as the file keeps them -- a byte a class, two a distance,
    #: four a height -- read into floats only at the point asked, so four
    #: planets fit a process in tens of megabytes, not hundreds.
    height: np.ndarray  # share of the rise, float32, (rows, cols)
    water: np.ndarray  # LAND / SEA / LAKE / RIVER, uint8
    form: np.ndarray  # uint8, codes of `forms`
    hardness: np.ndarray  # uint8, 255 = 1.0
    river_m: np.ndarray  # to the nearest river or lake, metres, uint16, capped
    temperature_c: np.ndarray  # int8
    rain: np.ndarray  # uint8, 255 = 1.0
    ice: np.ndarray  # bool
    forms: tuple[str, ...]  # the passport's table: code -> id
    mountain_level: float  # share above which the land is mountain
    river_cap_m: float  # the distance raster's reach: at it, no fresh water in sight
    wet: bool  # whether any sea or lake holds water
    #: The sketch's grid: the height share on a coarser grid, and the
    #: lakes on it as (row, col) cells.
    grid: np.ndarray
    lakes: frozenset[tuple[int, int]]

    #: What the noise field promised and the sketch still speaks (D-323):
    #: the sea's level is zero by construction, a basin is a tile reading
    #: at or under it, a peak a reading over the mountain line.
    sea_level: float = 0.0
    rivers: tuple = ()

    @property
    def rows(self) -> int:
        return int(self.height.shape[0])

    @property
    def cols(self) -> int:
        return int(self.height.shape[1])

    def grid_latitudes(self) -> list[float]:
        """The latitude at the middle of each row of the sketch's grid, south to north."""
        rows = int(self.grid.shape[0])
        return [-90.0 + (row + 0.5) * (180.0 / rows) for row in range(rows)]

    def grid_warmth(self) -> list[int]:
        """The mean temperature of each row of the sketch's grid, south to
        north: the climate the globe tints, the field's own and not a curve
        of the latitude the nodes no longer read."""
        rows = int(self.grid.shape[0])
        factor = max(1, self.rows // rows)
        used = rows * factor
        means = (
            self.temperature_c[:used]
            .astype(float)
            .reshape(rows, factor, self.cols)
            .mean(axis=(1, 2))
        )
        return [int(round(float(value))) for value in means]

    @property
    def peak_level(self) -> float:
        return self.mountain_level

    @property
    def basin_level(self) -> float:
        return 0.0

    # --- cells ------------------------------------------------------------

    def cell(self, lat: float, lon: float) -> tuple[int, int]:
        row = int((lat + 90.0) / (180.0 / self.rows))
        col = int((lon + 180.0) / (360.0 / self.cols))
        return min(self.rows - 1, max(0, row)), col % self.cols

    def centre(self, row: int, col: int) -> tuple[float, float]:
        return (
            -90.0 + (row + 0.5) * (180.0 / self.rows),
            -180.0 + (col + 0.5) * (360.0 / self.cols),
        )

    def _bilinear(self, raster: np.ndarray, lat: float, lon: float) -> float:
        fr = min(self.rows - 1.0, max(0.0, (lat + 90.0) / (180.0 / self.rows) - 0.5))
        fc = (((lon + 180.0) / (360.0 / self.cols) - 0.5) % self.cols + self.cols) % self.cols
        r0 = int(math.floor(fr))
        r1 = min(self.rows - 1, r0 + 1)
        c0 = int(math.floor(fc))
        c1 = (c0 + 1) % self.cols
        t = fr - r0
        u = fc - c0
        return float(
            raster[r0, c0] * (1 - t) * (1 - u)
            + raster[r0, c1] * (1 - t) * u
            + raster[r1, c0] * t * (1 - u)
            + raster[r1, c1] * t * u
        )

    # --- what the engine asks -------------------------------------------

    def height_at(self, lat: float, lon: float) -> float:
        """The share of the rise at a point, between the cells; the sea negative."""
        return self._bilinear(self.height, lat, lon)

    def relief(self, lat: float, lon: float) -> float:
        """How far above the sea the point stands, as a share of the rise, 0..1."""
        return max(0.0, min(1.0, self.height_at(lat, lon)))

    def is_sea(self, lat: float, lon: float) -> bool:
        return self.water[self.cell(lat, lon)] == SEA

    def is_lake(self, lat: float, lon: float) -> bool:
        return self.water[self.cell(lat, lon)] == LAKE

    def is_water(self, lat: float, lon: float) -> bool:
        return self.water[self.cell(lat, lon)] in (SEA, LAKE)

    def is_river(self, lat: float, lon: float) -> bool:
        return self.water[self.cell(lat, lon)] == RIVER

    def is_mountain(self, lat: float, lon: float) -> bool:
        return not self.is_water(lat, lon) and self.height_at(lat, lon) >= self.mountain_level

    def form_at(self, lat: float, lon: float) -> str:
        return self.forms[int(self.form[self.cell(lat, lon)])]

    def hardness_at(self, lat: float, lon: float) -> float:
        return float(self.hardness[self.cell(lat, lon)]) / BYTE

    def temperature_at(self, lat: float, lon: float) -> float:
        return self._bilinear(self.temperature_c, lat, lon)

    def rain_at(self, lat: float, lon: float) -> float:
        return self._bilinear(self.rain, lat, lon) / BYTE

    def river_distance_deg(self, lat: float, lon: float) -> float:
        """How far the nearest fresh water runs, in degrees of arc -- infinite
        beyond the raster's reach."""
        metres = float(self.river_m[self.cell(lat, lon)])
        if metres >= self.river_cap_m:
            return math.inf
        return math.degrees(metres / self.radius_m)

    def river_crossed(self, a: tuple[float, float], b: tuple[float, float]) -> bool:
        """Whether the straight way from `a` to `b` steps on a river cell."""
        phi = math.radians(a[0])
        dlat = b[0] - a[0]
        dlon = ((b[1] - a[1] + 180.0) % 360.0) - 180.0
        metres = self.radius_m * math.radians(math.hypot(dlat, dlon * math.cos(phi)))
        steps = max(2, int(math.ceil(metres / (0.5 * self.step_m))))
        for k in range(steps + 1):
            share = k / steps
            if self.is_river(a[0] + dlat * share, a[1] + dlon * share):
                return True
        return False

    def land_share(self) -> float:
        weight = np.cos(np.radians(-90.0 + (np.arange(self.rows) + 0.5) * (180.0 / self.rows)))
        land = (self.water != SEA).astype(float)
        return float((land * weight[:, None]).sum() / (weight.sum() * self.cols))

    def tile(self, row: int, col: int) -> np.ndarray:
        """The height share on the client's lattice of a tile (D-323): `TILE_N + 1`
        a side from the tile's south-west corner, lakes sunk under zero. The
        same bilinear reading as `height_at`, over the whole lattice at once."""
        lat0, lon0 = relief.tile_origin(row, col)
        steps = np.arange(relief.TILE_N + 1) * (relief.TILE_DEG / relief.TILE_N)
        lat = np.minimum(90.0, lat0 + steps)[:, None]
        lon = (((lon0 + steps + 180.0) % 360.0) - 180.0)[None, :]
        fr = np.clip((lat + 90.0) / (180.0 / self.rows) - 0.5, 0.0, self.rows - 1.0)
        fc = ((lon + 180.0) / (360.0 / self.cols) - 0.5) % self.cols
        r0 = np.floor(fr).astype(int)
        r1 = np.minimum(self.rows - 1, r0 + 1)
        c0 = np.floor(fc).astype(int)
        c1 = (c0 + 1) % self.cols
        t = fr - r0
        u = fc - c0
        h = self.height
        out = (
            h[r0, c0] * (1 - t) * (1 - u)
            + h[r0, c1] * (1 - t) * u
            + h[r1, c0] * t * (1 - u)
            + h[r1, c1] * t * u
        )
        cell_r = np.clip(((lat + 90.0) / (180.0 / self.rows)).astype(int), 0, self.rows - 1)
        cell_c = ((lon + 180.0) / (360.0 / self.cols)).astype(int) % self.cols
        lake = self.water[cell_r, cell_c] == LAKE
        return np.where(lake, np.minimum(out, LAKE_SINK), out)


# --- loading ----------------------------------------------------------------


def build_dir(constants: Constants) -> Path:
    """Where the vault's build lies: beside the constants the engine runs on.

    `Constants.source` is the path of `constants.json` the set was read from
    (`loader.load_constants`), with `+overrides` appended by an edit; a set
    made in a test carries a label instead, and then there is no build to
    read a field from -- said so, not guessed at.
    """
    source = Path(constants.source.split("+")[0])
    if not source.is_file():
        raise FieldMissing(
            f"the constant set {constants.source!r} was not read from a build, "
            "so there is no field beside it"
        )
    return source.resolve().parent


def _expected(constants: Constants, planet: Planet) -> dict[str, float]:
    """The passport's numbers as the registry would have them."""
    planets = list(Planet)
    temp = constants[R.SITE_TEMP_RANGE]
    return {
        "seed": float(int(constants[R.TERRAIN_SEED]) * len(planets) + planets.index(planet)),
        "sea_share": float(constants[R.TERRAIN_SEA_SHARE].get(planet.value, 0.0)),
        "relief_m": float(constants[R.TERRAIN_RELIEF_M]),
        "step_m": float(constants[R.TERRAIN_STEP_M]),
        "version": float(constants[R.TERRAIN_VERSION]),
        "warm_c": float(temp.max),
        "cold_c": float(temp.min),
    }


def _check_passport(params: dict, expected: dict[str, float], planet: str) -> None:
    """A field built under other numbers than the registry's is refused:
    a rise or a sea share edited in the vault without a rebuilt field would
    otherwise scale the world quietly (review, 2026-09-09)."""
    wrong = []
    for key, want in expected.items():
        got = params.get(key)
        if got is None or abs(float(got) - want) > PASSPORT_TOLERANCE * max(1.0, abs(want)):
            wrong.append(f"{key}: passport {got}, registry {want:g}")
    if wrong:
        raise FieldStale(
            f"the field of {planet} was built under other numbers -- "
            + "; ".join(wrong)
            + f". Rebuild it in the vault: `python tools/landscape.py build --planet {planet}`"
        )


@functools.cache
def _loaded(directory: str, planet: str, mountain_share: float, expected: tuple) -> Field:
    root = Path(directory) / "field"
    arrays = root / f"{planet}.npz"
    passport = root / f"{planet}.json"
    if not arrays.exists() or not passport.exists():
        raise FieldMissing(
            f"no field for {planet} in {root}: build it in the vault with "
            f"`python tools/landscape.py build --planet {planet}` and sync"
        )
    meta = json.loads(passport.read_text(encoding="utf-8"))
    params = meta["params"]
    _check_passport(params, dict(expected), planet)
    relief_m = float(params["relief_m"])
    with np.load(arrays) as z:
        height = (z["height_m"].astype(np.float32) / np.float32(relief_m)).astype(np.float32)
        water = z["water"].astype(np.uint8)
        form = z["form"].astype(np.uint8)
        hardness = z["hardness"].astype(np.uint8)
        river_m = z["river_m"].astype(np.uint16)
        temperature = z["temperature_c"].astype(np.int8)
        rain = z["rain"].astype(np.uint8)
        ice = z["ice"].astype(bool)
    land = water != SEA
    #: The mountain line as the noise field cut it: the share of the land
    #: above it is `terrain.mountain_share`.
    heights_on_land = height[land]
    mountain_level = (
        float(np.quantile(heights_on_land, 1.0 - mountain_share))
        if heights_on_land.size and 0.0 < mountain_share < 1.0
        else 2.0
    )
    grid, lakes = _sketch_grid(height, water)
    return Field(
        planet=planet,
        seed=int(params["seed"]),
        step_m=float(params["step_m"]),
        radius_m=float(params["radius_m"]),
        relief_m=relief_m,
        height=height,
        water=water,
        form=form,
        hardness=hardness,
        river_m=river_m,
        temperature_c=temperature,
        rain=rain,
        ice=ice,
        forms=tuple(str(entry["id"]) for entry in meta.get("forms", [])),
        mountain_level=mountain_level,
        river_cap_m=float(river_m.max()),
        wet=bool((water == SEA).any() or (water == LAKE).any()),
        grid=grid,
        lakes=lakes,
    )


def _sketch_grid(
    height: np.ndarray, water: np.ndarray
) -> tuple[np.ndarray, frozenset[tuple[int, int]]]:
    """The sketch's coarser grid: block means of the height on the side the
    block's majority stands -- a coast block that is mostly land reads as
    land, however deep the sea in its other half -- and the blocks that are
    mostly lake."""
    rows, cols = height.shape
    factor = max(1, int(math.ceil(rows / SKETCH_ROWS)))
    r = rows // factor * factor
    c = cols // factor * factor
    shape = (r // factor, factor, c // factor, factor)
    blocks = height[:r, :c].astype(float).reshape(shape)
    land = (water[:r, :c] != SEA).reshape(shape)
    land_share = land.mean(axis=(1, 3))
    mostly_land = land_share > 0.5
    #: The mean over the majority's own cells, so a coast block is neither
    #: dragged under by its sea nor lifted by its land.
    land_mean = np.where(land, blocks, 0.0).sum(axis=(1, 3)) / np.maximum(land.sum(axis=(1, 3)), 1)
    sea_mean = np.where(~land, blocks, 0.0).sum(axis=(1, 3)) / np.maximum(
        (~land).sum(axis=(1, 3)), 1
    )
    grid = np.where(
        mostly_land, np.maximum(land_mean, SKETCH_SHORE), np.minimum(sea_mean, -SKETCH_SHORE)
    )
    lake = (water[:r, :c] == LAKE).reshape(shape).mean(axis=(1, 3))
    lakes = frozenset((int(i), int(j)) for i, j in zip(*np.nonzero(lake > 0.5), strict=True))
    return grid, lakes


def of(constants: Constants, planet: Planet) -> Field:
    """The planet's field, read once per build and kept for the process."""
    expected = tuple(sorted(_expected(constants, planet).items()))
    return _loaded(
        str(build_dir(constants)),
        planet.value,
        float(constants[R.TERRAIN_MOUNTAIN_SHARE]),
        expected,
    )


def preload(constants: Constants) -> None:
    """Every planet's field, read now: a build without one, or with one
    built under other numbers, stops the process here rather than the
    first `look`."""
    for planet in Planet:
        of(constants, planet)
