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

Since D-328 the grid is HEALPix: `12 nside**2` cells of equal area, one
flat array, no rows and no columns, and a cell the same size at the pole as
at the equator. Finding a point's cell is arithmetic (`healpix.ang2pix`),
not a lookup, and the projection is checked against the passport's probe
when the file is read -- the vault and the game hold the same arithmetic in
two repositories that cannot import one another.

Rasters are read with the cell's own value where the thing is categorical
(water, form) and between the four cells around the point where it is a
quantity (height): a coast is a line through the cells, not a staircase of
them. HEALPix has no four corners of a square, so the four are two rings of
equal latitude and two places along each (`healpix.Rings.corners`).

The sketch the client draws is a coarse lattice of latitude and longitude
sampled off the field, and a tile is a window of it on the client's own
lattice.
"""

from __future__ import annotations

import functools
import json
import math
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np

from src import healpix, relief
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
#: The water raster's classes by code, as the passport names them.
WATER_NAMES = ("land", "sea", "lake", "river")
#: A byte-scaled share: 255 is one.
BYTE = 255.0
#: A byte raster's word for "no class": the biome raster's water (plan wave 5).
NO_CLASS = 255
#: How far off zero a sketch cell is held on the side its land majority says,
#: when its mean height would put it on the other: a coast cell reads as the
#: raster does, not as its average.
SKETCH_SHORE = 1e-3
#: And the same hair for a coast texel of the picture's rasters, which are
#: thinned the same way and hold the same contract (`engine.rasters._shore`).
RASTER_SHORE = SKETCH_SHORE
#: How many points across a sketch cell are sampled off the field to make
#: it: the field is a flat run of cells with no rows to average, so the
#: sketch is drawn by asking it, and a sample every few cells is enough for
#: a picture of a hundred and twenty-eight rows.
SKETCH_SAMPLES = 6


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
    #: The side of a cell, metres -- one number for the whole planet, because
    #: every cell holds the same area (D-328). What the vault was asked for
    #: (`terrain.step_m`) is what the passport is checked against; this is
    #: what the grid came out at, and it is what "a cell" means everywhere
    #: below.
    step_m: float
    radius_m: float
    relief_m: float
    #: The grid's fineness: `12 nside**2` cells (D-328).
    nside: int
    #: The cells laid out by ring of equal latitude, for reading a quantity
    #: between them.
    rings: healpix.Rings
    #: The rasters as the file keeps them -- a byte a class, two a distance,
    #: four a height -- read into floats only at the point asked, so a planet
    #: is tens of megabytes and not hundreds: Terra's whole field is thirty.
    height: np.ndarray  # share of the rise, float32, (cells,)
    water: np.ndarray  # LAND / SEA / LAKE / RIVER, uint8
    form: np.ndarray  # uint8, codes of `forms`
    hardness: np.ndarray  # uint8, 255 = 1.0
    river_m: np.ndarray  # to the nearest river or lake, metres, uint16, capped
    #: How much land drains through the river a cell could be the bank of,
    #: km2 -- the catchment of the biggest river among its neighbours, and
    #: nought where there is none. A river's width is read off it (landscape
    #: plan §9.2: a river is ground and widens downstream). Made by the vault
    #: and carried in the file since D-328: who a cell's neighbours are is a
    #: property of the grid, and the game keeps no table of them.
    river_flow_km2: np.ndarray  # float32, 0 away from every river
    sea_m: np.ndarray  # to the nearest sea, metres, uint16, capped
    temperature_c: np.ndarray  # int8
    rain: np.ndarray  # uint8, 255 = 1.0
    ice: np.ndarray  # bool
    province: np.ndarray  # uint8: 0 none, k the province with code k
    forms: tuple[str, ...]  # the passport's table: code -> id
    #: The provinces of the planet by code (landscape plan, wave 3): the id
    #: a found node is stamped with, and the vein multiplier the find rolls by.
    provinces: tuple[str, ...]
    province_vein_k: tuple[float, ...]
    #: The faces each province favours (landscape plan wave 8): the ids of
    #: the facets that turn up here oftener than elsewhere.
    province_favours: tuple[tuple[str, ...], ...]
    mountain_level: float  # share above which the land is mountain
    river_cap_m: float  # the distance raster's reach: at it, no fresh water in sight
    sea_cap_m: float  # the same for the sea
    wet: bool  # whether any sea or lake holds water
    #: The sketch's grid: the height share on a coarse lattice of latitude
    #: and longitude sampled off the field, the lakes on it as (row, col)
    #: cells, and the mean warmth of each of its rows.
    grid: np.ndarray
    lakes: frozenset[tuple[int, int]]
    warmth: tuple[int, ...]

    #: What the noise field promised and the sketch still speaks (D-323):
    #: the sea's level is zero by construction, a basin is a tile reading
    #: at or under it, a peak a reading over the mountain line.
    sea_level: float = 0.0
    rivers: tuple = ()

    @property
    def cells(self) -> int:
        """How many cells the planet is cut into."""
        return int(self.height.shape[0])

    def grid_latitudes(self) -> list[float]:
        """The latitude at the middle of each row of the sketch's grid, south to north."""
        rows = int(self.grid.shape[0])
        return [-90.0 + (row + 0.5) * (180.0 / rows) for row in range(rows)]

    def grid_warmth(self) -> list[int]:
        """The mean temperature of each row of the sketch's grid, south to
        north: the climate the globe tints, the field's own and not a curve
        of the latitude the nodes no longer read."""
        return list(self.warmth)

    @property
    def peak_level(self) -> float:
        return self.mountain_level

    @property
    def basin_level(self) -> float:
        return 0.0

    # --- cells ------------------------------------------------------------

    def cell(self, lat: float, lon: float) -> int:
        """The cell a point falls in."""
        return int(healpix.ang2pix(self.nside, lat, lon))

    def cells_at(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        """The cells a run of points falls in.

        Finding one cell is a couple of dozen microseconds -- the projection
        is arithmetic, but it is numpy's arithmetic, and the cost is the call
        and not the sums. Asked for a hundred points at once it is the sums
        again. Everything that walks a line over the ground (`horizon`,
        `facet`, `explore.aim`) asks for its whole walk in one go.
        """
        return healpix.ang2pix(self.nside, lat, lon)

    @cached_property
    def centres(self) -> tuple[np.ndarray, np.ndarray]:
        """The middle of every cell, degrees. Made when first asked and not
        before: it is twelve megabytes a planet, and only `centre` and the
        thinning of the picture's rasters read it."""
        return healpix.centres(self.nside, self.rings)

    def centre(self, cell: int) -> tuple[float, float]:
        """The middle of a cell, degrees."""
        lat, lon = self.centres
        return float(lat[cell]), float(lon[cell])

    def between(self, raster: np.ndarray, lat: float, lon: float) -> float:
        """A quantity between the cells around a point (`healpix.Rings`)."""
        return float(self.rings.between(raster, lat, lon))

    def reliefs(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        """How far above the sea a run of points stands, shares of the rise,
        the sea at nought. One reading for the whole walk (see `cells`)."""
        share = self.rings.between(self.height, lat, lon)
        return np.where(self.water[self.cells_at(lat, lon)] == SEA, 0.0, np.clip(share, 0.0, 1.0))

    # --- what the engine asks -------------------------------------------

    def height_at(self, lat: float, lon: float) -> float:
        """The share of the rise at a point, between the cells; the sea negative."""
        return self.between(self.height, lat, lon)

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

    def ice_at(self, lat: float, lon: float) -> bool:
        """Whether the cap lies here: cold and wet, or very cold (the field's
        `terrain.ice_*`), read as the field decided it, not re-derived.

        Since D-329 the raster also covers **sea** colder than `terrain.ice_c`
        -- the fast ice of a frozen ocean, which Aurora is made of and Terra
        has at its poles. That does not reach the biome: `classify` asks
        `is_water` first and a sea cell has none, so this answers for the
        land alone wherever anybody asks it.
        """
        return bool(self.ice[self.cell(lat, lon)])

    def sea_distance_m(self, lat: float, lon: float) -> float:
        """How far the nearest sea lies, metres -- infinite beyond the raster's reach."""
        metres = float(self.sea_m[self.cell(lat, lon)])
        return math.inf if metres >= self.sea_cap_m else metres

    def form_at(self, lat: float, lon: float) -> str:
        return self.forms[int(self.form[self.cell(lat, lon)])]

    def province_at(self, lat: float, lon: float) -> str | None:
        """The id of the province a point lies in, or None on the sea and on
        a planet without provinces."""
        code = int(self.province[self.cell(lat, lon)])
        return self.provinces[code - 1] if 0 < code <= len(self.provinces) else None

    def province_favours_at(self, lat: float, lon: float) -> tuple[str, ...]:
        """The faces the province of this point favours, or nothing where
        there is no province -- or where the field was built before they
        travelled with it."""
        code = int(self.province[self.cell(lat, lon)])
        if 0 < code <= len(self.province_favours):
            return self.province_favours[code - 1]
        return ()

    def province_vein_k_at(self, lat: float, lon: float) -> float:
        """How much likelier a vein is here than the biome says: the
        province's multiplier, one where there is no province."""
        code = int(self.province[self.cell(lat, lon)])
        return self.province_vein_k[code - 1] if 0 < code <= len(self.province_vein_k) else 1.0

    def hardness_at(self, lat: float, lon: float) -> float:
        return float(self.hardness[self.cell(lat, lon)]) / BYTE

    def temperature_at(self, lat: float, lon: float) -> float:
        return self.between(self.temperature_c, lat, lon)

    def rain_at(self, lat: float, lon: float) -> float:
        return self.between(self.rain, lat, lon) / BYTE

    def river_distance_deg(self, lat: float, lon: float) -> float:
        """How far the nearest fresh water runs, in degrees of arc -- infinite
        beyond the raster's reach."""
        metres = float(self.river_m[self.cell(lat, lon)])
        if metres >= self.river_cap_m:
            return math.inf
        return math.degrees(metres / self.radius_m)

    def river_distance_m(self, lat: float, lon: float) -> float:
        """How far the nearest fresh water runs, metres -- infinite beyond the
        raster's reach, as `sea_distance_m` is."""
        metres = float(self.river_m[self.cell(lat, lon)])
        return math.inf if metres >= self.river_cap_m else metres

    def river_crossed(self, a: tuple[float, float], b: tuple[float, float]) -> bool:
        """Whether the straight way from `a` to `b` steps on a river cell.

        The whole way is read in one ask: a cell of the equal-area grid is
        found by arithmetic, and the cost of that arithmetic is the call, not
        the sums (`cells_at`).
        """
        phi = math.radians(a[0])
        dlat = b[0] - a[0]
        dlon = ((b[1] - a[1] + 180.0) % 360.0) - 180.0
        metres = self.radius_m * math.radians(math.hypot(dlat, dlon * math.cos(phi)))
        steps = max(2, int(math.ceil(metres / (0.5 * self.step_m))))
        share = np.arange(steps + 1) / steps
        lat = a[0] + dlat * share
        lon = ((a[1] + dlon * share + 180.0) % 360.0) - 180.0
        return bool((self.water[self.cells_at(lat, lon)] == RIVER).any())

    def land_share(self) -> float:
        """The share of the planet that is not sea. A share of the cells is a
        share of the area: every cell holds the same square metres (D-328)."""
        return float((self.water != SEA).mean())

    def tile(self, row: int, col: int) -> np.ndarray:
        """The height share on the client's lattice of a tile (D-323): `TILE_N + 1`
        a side from the tile's south-west corner, lakes sunk under zero. The
        same reading between the cells as `height_at`, over the whole lattice
        at once."""
        lat0, lon0 = relief.tile_origin(row, col)
        steps = np.arange(relief.TILE_N + 1) * (relief.TILE_DEG / relief.TILE_N)
        lat = np.minimum(90.0, lat0 + steps)[:, None]
        lon = (((lon0 + steps + 180.0) % 360.0) - 180.0)[None, :]
        out = self.rings.between(self.height, lat, lon)
        lake = self.water[healpix.ang2pix(self.nside, lat, lon)] == LAKE
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
    """The passport's numbers as the registry would have them.

    Seed and temperature are read **by planet**: one seed for four worlds
    could not be re-rolled for one of them, and one pair of warm and cold
    ends left Pyroxis with ice on a fifth of its land and Aurora with dunes
    (owner, 2026-09-10). What the passport carries did not change shape --
    it is still one seed and one pair per field -- only where they come from.
    """
    temp = constants[R.TERRAIN_TEMP_RANGE][planet.value]
    return {
        "seed": float(constants[R.TERRAIN_SEED][planet.value]),
        "sea_level": float(constants[R.TERRAIN_SEA_LEVEL][planet.value]),
        "relief_m": float(constants[R.TERRAIN_RELIEF_M]),
        "step_m": float(constants[R.TERRAIN_STEP_M]),
        "version": float(constants[R.TERRAIN_VERSION]),
        "warm_c": float(temp["max"]),
        "cold_c": float(temp["min"]),
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


def _check_probe(meta: dict, nside: int, planet: str) -> None:
    """The vault's own points against this module's projection.

    The grid lives in two repositories that cannot import one another, so
    the arithmetic that finds a point's cell exists twice and could drift
    apart without anything falling over -- a world read a kilometre off is
    still a world. The vault writes a few points and the cells it put them
    in; if they disagree here, the server stops (D-328).
    """
    probe = meta.get("probe")
    if not probe:
        raise FieldStale(
            f"the field of {planet} carries no projection probe: it was built "
            "before the equal-area grid. Rebuild it in the vault: "
            f"`python tools/landscape.py build --planet {planet}`"
        )
    want = np.asarray(probe["cell"], dtype=np.int64)
    got = healpix.ang2pix(nside, np.asarray(probe["lat"]), np.asarray(probe["lon"]))
    apart = int((got != want).sum())
    if apart:
        first = int(np.flatnonzero(got != want)[0])
        raise FieldStale(
            f"the field of {planet} was cut by another projection than this one: "
            f"{apart} of {want.size} probe points land elsewhere -- "
            f"({probe['lat'][first]:.4f}, {probe['lon'][first]:.4f}) in cell "
            f"{int(got[first])} here and {int(want[first])} in the vault. "
            "`src/healpix.py` and the vault's `tools/field/healpix.py` have drifted apart"
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
    grid = meta.get("grid") or {}
    nside = int(grid.get("nside", 0))
    if grid.get("kind") != "healpix" or nside < 1:
        raise FieldStale(
            f"the field of {planet} is on {grid.get('kind', 'an unnamed')} grid, "
            "not the equal-area one. Rebuild it in the vault: "
            f"`python tools/landscape.py build --planet {planet}`"
        )
    _check_probe(meta, nside, planet)
    relief_m = float(params["relief_m"])
    with np.load(arrays) as z:
        height = (z["height_m"].astype(np.float32) / np.float32(relief_m)).astype(np.float32)
        water = z["water"].astype(np.uint8)
        form = z["form"].astype(np.uint8)
        hardness = z["hardness"].astype(np.uint8)
        river_m = z["river_m"].astype(np.uint16)
        flow_km2 = z["flow_km2"].astype(np.float32)
        sea_m = z["sea_m"].astype(np.uint16)
        temperature = z["temperature_c"].astype(np.int8)
        rain = z["rain"].astype(np.uint8)
        ice = z["ice"].astype(bool)
        province = (
            z["province"].astype(np.uint8)
            if "province" in z
            else np.zeros(water.shape, dtype=np.uint8)
        )
    if height.shape != (healpix.npix(nside),):
        raise FieldStale(
            f"the field of {planet} holds {height.size} cells where nside {nside} "
            f"asks for {healpix.npix(nside)}"
        )
    land = water != SEA
    provinces_table = list(meta.get("provinces", []))
    #: The mountain line as the noise field cut it: the share of the land
    #: above it is `terrain.mountain_share`. A share of the cells is a share
    #: of the area now (D-328), so the quantile needs no weights.
    heights_on_land = height[land]
    mountain_level = (
        float(np.quantile(heights_on_land, 1.0 - mountain_share))
        if heights_on_land.size and 0.0 < mountain_share < 1.0
        else 2.0
    )
    rings = healpix.Rings(nside)
    #: Laid out now rather than on the first request: reading a field is a
    #: start-up cost by design (`preload`), and a `look` is not. The sketch
    #: below is the first thing to ask for it.
    sketch, lakes, warmth = _sketch(rings, height, water, temperature)
    return Field(
        planet=planet,
        seed=int(params["seed"]),
        step_m=float(grid.get("side_m") or params["step_m"]),
        radius_m=float(params["radius_m"]),
        relief_m=relief_m,
        nside=nside,
        rings=rings,
        height=height,
        water=water,
        form=form,
        hardness=hardness,
        river_m=river_m,
        river_flow_km2=flow_km2,
        sea_m=sea_m,
        temperature_c=temperature,
        rain=rain,
        ice=ice,
        province=province,
        forms=tuple(str(entry["id"]) for entry in meta.get("forms", [])),
        provinces=tuple(str(entry["id"]) for entry in provinces_table),
        province_vein_k=tuple(float(entry.get("vein_k", 1.0)) for entry in provinces_table),
        province_favours=tuple(
            tuple(str(fid) for fid in (entry.get("favours") or ())) for entry in provinces_table
        ),
        mountain_level=mountain_level,
        river_cap_m=float(river_m.max()),
        sea_cap_m=float(sea_m.max()),
        wet=bool((water == SEA).any() or (water == LAKE).any()),
        grid=sketch,
        lakes=lakes,
        warmth=warmth,
    )


def _sketch(
    rings: healpix.Rings, height: np.ndarray, water: np.ndarray, temperature: np.ndarray
) -> tuple[np.ndarray, frozenset[tuple[int, int]], tuple[int, ...]]:
    """The sketch's coarse lattice, sampled off the field.

    A lattice of latitude and longitude, because that is what the globe
    draws by and what the client already speaks -- the field itself has no
    rows to average since D-328. Each cell of it is read at
    `SKETCH_SAMPLES` points a side, and its height is the mean over the
    side its majority stands on: a coast cell that is mostly land reads as
    land, however deep the sea in its other half.
    """
    rows = SKETCH_ROWS
    cols = 2 * rows
    grain = SKETCH_SAMPLES
    #: The sample points of every cell of the lattice at once.
    within = (np.arange(grain) + 0.5) / grain
    lat = (-90.0 + (np.arange(rows)[:, None] + within[None, :]) * (180.0 / rows)).reshape(-1)
    lon = (-180.0 + (np.arange(cols)[:, None] + within[None, :]) * (360.0 / cols)).reshape(-1)
    cells = healpix.ang2pix(rings.nside, lat[:, None], lon[None, :])
    shape = (rows, grain, cols, grain)
    blocks = height[cells].astype(float).reshape(shape)
    land = (water[cells] != SEA).reshape(shape)
    land_share = land.mean(axis=(1, 3))
    mostly_land = land_share > 0.5
    #: The mean over the majority's own samples, so a coast cell is neither
    #: dragged under by its sea nor lifted by its land.
    land_mean = np.where(land, blocks, 0.0).sum(axis=(1, 3)) / np.maximum(land.sum(axis=(1, 3)), 1)
    sea_mean = np.where(~land, blocks, 0.0).sum(axis=(1, 3)) / np.maximum(
        (~land).sum(axis=(1, 3)), 1
    )
    sketch = np.where(
        mostly_land, np.maximum(land_mean, SKETCH_SHORE), np.minimum(sea_mean, -SKETCH_SHORE)
    )
    lake = (water[cells] == LAKE).reshape(shape).mean(axis=(1, 3))
    lakes = frozenset((int(i), int(j)) for i, j in zip(*np.nonzero(lake > 0.5), strict=True))
    #: The warmth of each row of the lattice: the climate the globe tints.
    warm = temperature[cells].astype(float).reshape(shape).mean(axis=(1, 2, 3))
    return sketch, lakes, tuple(int(round(float(value))) for value in warm)


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
