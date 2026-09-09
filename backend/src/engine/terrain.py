# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What a planet is made of, read at a point: sea, mountain, river, climate (D-319).

A planet has a **field** -- continents, seas, mountains, rivers, lakes,
landforms, climate -- made by the vault's pipeline and read from its build
(`src.field`, landscape plan wave 2) wherever a node stands. Nothing is
rolled: a node at a point carries what the field says there, so the globe
and the ground agree, and a river drawn on the map is a river at the node
beside it (`terrain.river_reach_km`).

The **climate** is the field's too: temperature by latitude, height, the
depth of the continent and the local weather, rain carried by the winds
and dropped on the windward slopes -- read off the rasters and scaled to
the vault's ranges (`site.temp_range`, `site.rain_range`). The diurnal
swing is the planet's (`planet.temp_swing`, D-261), and the hour is the
node's own since D-319: the longitude sets when its noon comes
(`climate.day_phase`).

The field is a file of the vault's build, read on first use and kept for
the process: two servers with one build read one world.
"""

from __future__ import annotations

import json
import math
from collections import OrderedDict

import numpy as np

from src import field as fields
from src import globe, relief
from src.constants import Constants
from src.constants import registry as R
from src.engine import ground, world
from src.models.world import Planet
from src.units import METRES_PER_KM, PERCENT

#: A mountain's mark on the node: the client draws it, the ground reads it.
MOUNTAIN = "mountain"
#: The field's other readings: each sign of the place reads the noise from
#: its own seed off the height's, so the woods do not simply follow the hills.
#: Until the facets of the landscape plan (wave 7) say where the woods are,
#: the noise does.
TEXTURE = 1
STONES = TEXTURE + 1


def field_of(constants: Constants, planet: Planet) -> fields.Field:
    """The planet's field, read once from the vault's build for these constants."""
    return fields.of(constants, planet)


def tile(constants: Constants, planet: Planet, row: int, col: int) -> dict | None:
    """A tile of the field as the client draws it (D-323, plan wave 2): the
    height share on the tile's lattice, lakes sunk under zero. The client
    keeps reading it as the `local` reading it cuts along `basin_level` and
    `peak_level` -- which the sketch sets to the sea's zero and the mountain
    line -- so the coast and the peaks it draws close up are the field's own
    (D-225). None off the planet."""
    rows, cols = relief.tile_counts()
    if not (0 <= row < rows and 0 <= col < cols):
        return None
    lat0, lon0 = relief.tile_origin(row, col)
    local = field_of(constants, planet).tile(row, col)
    return {
        "row": row,
        "col": col,
        "lat0": lat0,
        "lon0": lon0,
        "step": relief.TILE_DEG / relief.TILE_N,
        "n": relief.TILE_N,
        "local": np.round(local.astype(float), relief.TILE_DECIMALS).tolist(),
    }


def tile_json(constants: Constants, planet: Planet, row: int, col: int) -> bytes | None:
    """A tile, written once: a constant of the vault must not be encoded
    on every request. The `relief.TILE_KEEP` most recent are kept."""
    field = field_of(constants, planet)
    key = (id(field), row, col)
    got = _TILE_JSON.get(key)
    if got is not None:
        _TILE_JSON.move_to_end(key)
        return got
    made = tile(constants, planet, row, col)
    if made is None:
        return None
    got = json.dumps(made, separators=(",", ":")).encode()
    _TILE_JSON[key] = got
    while len(_TILE_JSON) > relief.TILE_KEEP:
        _TILE_JSON.popitem(last=False)
    return got


#: Tiles as bytes, by field and place; the fields live for the process
#: (`_built`), so their ids are stable keys.
_TILE_JSON: OrderedDict[tuple[int, int, int], bytes] = OrderedDict()


def is_land(constants: Constants, planet: Planet, lat: float, lon: float) -> bool:
    """Whether a node may stand here: not in the sea, not in a lake, not past the last latitude."""
    if abs(lat) > float(constants[R.MAP_CITY_LAT_MAX]):
        return False
    return not field_of(constants, planet).is_water(lat, lon)


def height_m(constants: Constants, planet: Planet, lat: float, lon: float) -> float:
    """How high above the sea a point stands, in metres (landscape plan, wave 1).

    The field reads a share of the land's rise (`Field.relief`, 0..1); the
    vault says how many metres that rise is (`terrain.relief_m`). The sea
    reads zero; a lake reads the land under it, as the climate does
    (`climate_at`), so a mountain lake is as high and as cold on every
    reading. This is the one place the share becomes a height, so a
    contour, a horizon and a slope all measure the same mountain. The field
    is scaled so that its highest summit is the whole rise.
    """
    field = field_of(constants, planet)
    if field.is_sea(lat, lon):
        return 0.0
    return field.relief(lat, lon) * float(constants[R.TERRAIN_RELIEF_M])


def river_reach_deg(constants: Constants, planet: Planet, lat: float) -> float:
    """`terrain.river_reach_km` as degrees of arc at this latitude's scale."""
    radius = globe.radius_m(constants, planet)
    return math.degrees(float(constants[R.TERRAIN_RIVER_REACH_KM]) * METRES_PER_KM / radius)


def marks_at(constants: Constants, planet: Planet, lat: float, lon: float) -> dict:
    """The place marks a node here carries: water, mountain, woods, stones, meadow.

    The river is not a chance but a fact of the map: within
    `terrain.river_reach_km` of a river line the node has river water, and
    nowhere else. The other signs are the field's noise cut at the vault's
    shares (`ground.forest_share` and its sisters), so a planet is forested
    to the share the vault says and the forest lies where the noise puts it,
    not scattered one node at a time.
    """
    field = field_of(constants, planet)
    reach = river_reach_deg(constants, planet, lat)
    near_river = field.river_distance_deg(lat, lon) <= reach
    #: A lake of the local relief (D-323) waters a node as a river does,
    #: within the same reach: the bowl is read around the node.
    near_lake = field.wet and any(field.is_lake(*p) for p in relief.around(lat, lon, reach))
    #: A second reading of the noise, offset from the height's, so the woods
    #: do not simply follow the mountains.
    texture = relief.noise_at(field.seed + TEXTURE, lat, lon)
    stony = relief.noise_at(field.seed + STONES, lat, lon)
    forest = float(constants[R.GROUND_FOREST_SHARE]) / PERCENT
    stones = float(constants[R.GROUND_STONES_SHARE]) / PERCENT
    meadow = float(constants[R.GROUND_MEADOW_SHARE]) / PERCENT
    mountain = field.is_mountain(lat, lon)
    return {
        world.WATER: world.RIVER if near_river else world.LAKE if near_lake else world.NO_WATER,
        MOUNTAIN: mountain,
        #: Nothing grows on a summit: the woods stop at the mountain line.
        ground.WOODS: texture < forest and not mountain,
        ground.STONES: stony < stones or mountain,
        ground.MEADOW: texture > 1 - meadow and not mountain,
    }


def by_latitude(constants: Constants, lat: float) -> float:
    """The mean temperature at sea level here: the vault's warm end at the
    equator, its cold end at the pole, by the square of the sine of the
    latitude -- the shape sunlight has."""
    warm, cold = constants[R.SITE_TEMP_RANGE].max, constants[R.SITE_TEMP_RANGE].min
    tilt = math.sin(math.radians(lat))
    return warm - (warm - cold) * tilt * tilt


def climate_of(constants: Constants, planet: Planet, lat: float, lon: float) -> tuple[float, float]:
    """The mean temperature and the rainfall at a point, read between the
    cells and not rounded: what the biome is sorted by.

    Both are the field's (plan wave 2): the temperature raster carries
    latitude, height, the depth of the continent and the local weather; the
    rain raster the winds' work, in shares of the vault's `site.rain_range`.
    """
    field = field_of(constants, planet)
    rain = constants[R.SITE_RAIN_RANGE]
    precipitation = rain.min + (rain.max - rain.min) * field.rain_at(lat, lon)
    return field.temperature_at(lat, lon), precipitation


def climate_at(constants: Constants, planet: Planet, lat: float, lon: float) -> tuple[int, int]:
    """The mean temperature and the rainfall a node here carries (D-261): the
    climate of the point in whole degrees and units, as the node writes it."""
    temperature, precipitation = climate_of(constants, planet, lat, lon)
    return round(temperature), round(precipitation)


def sketch(constants: Constants, planet: Planet) -> dict:
    """The field as the client draws it from afar: a coarser grid of the
    height, its lakes, the tiling it asks the close ground by.

    Everybody's from the world's first day (D-319): the shape of a planet is
    arithmetic over the vault, not intelligence, and hiding it would hide the
    one thing that makes a farmer walk along a river.

    Drawn once per field: the answer is a constant of the vault, and a
    public route must not rebuild thirty thousand numbers per request.
    """
    field = field_of(constants, planet)
    if id(field) not in _SKETCHES:
        _SKETCHES[id(field)] = _sketched(constants, planet, field)
    return _SKETCHES[id(field)]


#: Sketches by the field they draw; the fields themselves live for the
#: process (`field.of`), so their ids are stable keys.
_SKETCHES: dict[int, dict] = {}


def _sketched(constants: Constants, planet: Planet, field: fields.Field) -> dict:
    rows, cols = field.grid.shape
    return {
        "rows": int(rows),
        "cols": int(cols),
        "sea_level": field.sea_level,
        "mountain_level": field.mountain_level,
        "grid": [
            [round(float(value), relief.TILE_DECIMALS) for value in row] for row in field.grid
        ],
        #: The rivers as lines are the vector layer's, a later wave of the
        #: plan (§9.2); the raster keeps them for the game meanwhile.
        "rivers": [],
        "lakes": sorted(list(cell) for cell in field.lakes),
        #: The tiling of the field (D-323): the client asks for the tiles
        #: under a close frame by this.
        "tile": {"deg": relief.TILE_DEG, "n": relief.TILE_N},
        "peak_level": field.peak_level,
        "basin_level": field.basin_level,
        "wet": field.wet,
        #: The climate field as the globe tints it (plan, "Climate field"):
        #: the sea-level mean of each row of the grid, warm to cold.
        "warmth": field.grid_warmth(),
    }
