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


class FieldMissing(RuntimeError):
    """The vault's build carries no field for this planet."""


@dataclass(frozen=True)
class Field:
    """A planet's field as the engine reads it. Heights are shares of the
    land's rise (`terrain.relief_m`): the sea negative, the highest summit one."""

    planet: str
    seed: int
    step_m: float
    radius_m: float
    relief_m: float
    height: np.ndarray  # share of the rise, (rows, cols)
    water: np.ndarray  # LAND / SEA / LAKE / RIVER
    form: np.ndarray
    hardness: np.ndarray  # [0.25, 1]
    river_m: np.ndarray  # to the nearest river or lake, metres, capped
    wet_m: np.ndarray  # to any water, metres, capped
    temperature_c: np.ndarray
    rain: np.ndarray  # [0, 1]
    ice: np.ndarray
    forms: tuple[str, ...]  # the passport's table: code -> id
    mountain_level: float  # share above which the land is mountain
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

    @property
    def wet(self) -> bool:
        return bool((self.water == SEA).any() or (self.water == LAKE).any())

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
        return float(self.hardness[self.cell(lat, lon)])

    def temperature_at(self, lat: float, lon: float) -> float:
        return self._bilinear(self.temperature_c, lat, lon)

    def rain_at(self, lat: float, lon: float) -> float:
        return self._bilinear(self.rain, lat, lon)

    def river_distance_deg(self, lat: float, lon: float) -> float:
        """How far the nearest fresh water runs, in degrees of arc -- infinite
        beyond the raster's reach."""
        metres = float(self.river_m[self.cell(lat, lon)])
        if metres >= float(self.river_m.max()):
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
    """Where the vault's build lies: beside the constants the engine runs on."""
    return Path(constants.source.split("+")[0]).resolve().parent


@functools.cache
def _loaded(directory: str, planet: str, mountain_share: float) -> Field:
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
    relief_m = float(params["relief_m"])
    with np.load(arrays) as z:
        height_m = z["height_m"].astype(float)
        water = z["water"].astype(np.uint8)
        form = z["form"].astype(np.uint8)
        hardness = z["hardness"].astype(float) / 255.0
        river_m = z["river_m"].astype(float)
        wet_m = z["wet_m"].astype(float)
        temperature = z["temperature_c"].astype(float)
        rain = z["rain"].astype(float) / 255.0
        ice = z["ice"].astype(bool)
    height = height_m / relief_m
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
        wet_m=wet_m,
        temperature_c=temperature,
        rain=rain,
        ice=ice,
        forms=tuple(str(entry["id"]) for entry in meta.get("forms", [])),
        mountain_level=mountain_level,
        grid=grid,
        lakes=lakes,
    )


def _sketch_grid(
    height: np.ndarray, water: np.ndarray
) -> tuple[np.ndarray, frozenset[tuple[int, int]]]:
    """The sketch's coarser grid: block means of the height, and the blocks
    that are mostly lake."""
    rows, cols = height.shape
    factor = max(1, int(math.ceil(rows / SKETCH_ROWS)))
    r = rows // factor * factor
    c = cols // factor * factor
    blocks = height[:r, :c].reshape(r // factor, factor, c // factor, factor)
    grid = blocks.mean(axis=(1, 3))
    lake = (
        (water[:r, :c] == LAKE).reshape(r // factor, factor, c // factor, factor).mean(axis=(1, 3))
    )
    lakes = frozenset((int(i), int(j)) for i, j in zip(*np.nonzero(lake > 0.5), strict=True))
    return grid, lakes


def of(constants: Constants, planet: Planet) -> Field:
    """The planet's field, read once per build and kept for the process."""
    return _loaded(
        str(build_dir(constants)), planet.value, float(constants[R.TERRAIN_MOUNTAIN_SHARE])
    )
