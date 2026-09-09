# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Which biome a point of the field is, and what the biome says about a node (D-321).

The hidden map of a planet is the relief and the climate (`terrain`); the biome
is their reading at a point, sorted into the classes the vault names
(`biome.names`): the zonal ones -- tundra, taiga, steppe, woodland, forest,
semidesert, desert, savanna, rainforest -- which the climate alone decides, the
azonal ones -- alpine, foothills, floodplain, coast, marsh -- which the land's
shape decides over the climate, and one for the whole of Aurora (ice) and
Pyroxis (cinder). The class decides what exploration may do from a node
(`biome.reach_m`), what a found node looks like (`biome.marks`,
`biome.vein_k`), how hot its day and cold its night (`biome.swing_c`) and what
it is called.

The sorting is the vault's, not this module's (landscape plan, wave 4): the
zonal classes are rectangles of temperature and rain in `biome.zonal`, the
azonal ones a table of landform to biome in `biome.azonal`, and the two
formless cases keep their bounds in `biome.bounds`. The field's preview reads
the same tables off the rasters as the file keeps them, so what the owner
tunes on the render is what the node gets -- up to the reading between the
cells, which is the node's alone. Nothing here is a number of its own.
"""

from __future__ import annotations

import numpy as np

from src import field as fields
from src.constants import Constants
from src.constants import registry as R
from src.engine import places, terrain
from src.models.world import Node, Planet
from src.units import METRES_PER_KM

COAST = "coast"
FLOODPLAIN = "floodplain"
FOREST = "forest"
STEPPE = "steppe"
DESERT = "desert"
TAIGA = "taiga"
TUNDRA = "tundra"
MARSH = "marsh"
FOOTHILLS = "foothills"
ALPINE = "alpine"
ICE = "ice"
CINDER = "cinder"

#: The node properties a found node carries from its biome.
BIOME = "biome"
TEMPERATURE_SWING = "temperature_swing"
#: The province a found node lies in (landscape plan, wave 3): an id of the
#: vault's table, named through renames like any thing of the world.
PROVINCE = "province"

#: Planets whose whole surface is one biome: the ice of Aurora (D-232), the
#: black fields of Pyroxis (D-233). Their relief still decides water and rock.
OF_PLANET: dict[Planet, str] = {Planet.AURORA: ICE, Planet.PYROXIS: CINDER}


def _bound(constants: Constants, name: str) -> float:
    return float(constants[R.BIOME_BOUNDS][name])


def _near_sea(constants: Constants, planet: Planet, lat: float, lon: float) -> bool:
    field = terrain.field_of(constants, planet)
    return field.sea_distance_m(lat, lon) <= _bound(constants, "coast_km") * METRES_PER_KM


def _inside(value: float, low: float, high: float, top: float) -> bool:
    """A rectangle's edge rule: the low edge is in, the high edge out --
    except the top of the plane, which no other row could claim."""
    return low <= value < high or (value == top and high == top)


def zonal(constants: Constants, temperature: float, rain: float) -> str:
    """The zonal biome of a climate: the row of `biome.zonal` whose rectangle
    holds the point. The vault build has checked the rows tile the plane, so
    a point inside it always lands; a point outside is pushed to its edge
    rather than left without a class."""
    rows = list(constants[R.BIOME_ZONAL].values())
    top_t = max(float(row["temp"][1]) for row in rows)
    top_r = max(float(row["rain"][1]) for row in rows)
    t = min(top_t, max(min(float(row["temp"][0]) for row in rows), temperature))
    r = min(top_r, max(min(float(row["rain"][0]) for row in rows), rain))
    for row in rows:
        if _inside(t, float(row["temp"][0]), float(row["temp"][1]), top_t) and _inside(
            r, float(row["rain"][0]), float(row["rain"][1]), top_r
        ):
            return str(row["biome"])
    raise ValueError(f"biome.zonal has no row for {temperature} C, rain {rain}")


def _near_river(constants: Constants, planet: Planet, lat: float, lon: float) -> bool:
    field = terrain.field_of(constants, planet)
    return field.river_distance_deg(lat, lon) <= terrain.river_reach_deg(constants, planet, lat)


def classify(constants: Constants, planet: Planet, lat: float, lon: float) -> str | None:
    """The biome at a point, or nothing where there is water.

    Read top-down the way a geographer would: the planets of one face, then
    the ice the field laid, then the mountain line, then the shape of the
    land (`biome.azonal`), then the water's edge and the bog, and the climate
    (`biome.zonal`) sorts whatever the land's shape did not claim.
    """
    field = terrain.field_of(constants, planet)
    if field.is_water(lat, lon):
        return None
    if planet in OF_PLANET:
        return OF_PLANET[planet]
    if field.ice_at(lat, lon):
        return ICE
    if field.is_mountain(lat, lon):
        return ALPINE
    azonal = constants[R.BIOME_AZONAL].get(field.form_at(lat, lon))
    if azonal:
        return azonal
    if _near_river(constants, planet, lat, lon):
        return FLOODPLAIN
    if _near_sea(constants, planet, lat, lon):
        return COAST
    temperature, rain = terrain.climate_of(constants, planet, lat, lon)
    if rain > _bound(constants, "wet") and field.relief(lat, lon) < _bound(
        constants, "marsh_relief"
    ):
        return MARSH
    return zonal(constants, temperature, rain)


#: The biome raster's word for water, which has no biome.
NONE = fields.NO_CLASS


def codes(constants: Constants) -> list[str]:
    """The biome raster's codes: the order of `biome.names`."""
    return list(constants[R.BIOME_NAMES])


def raster(constants: Constants, planet: Planet) -> np.ndarray:
    """The whole field sorted at once, for the picture (landscape plan wave 5):
    a byte a cell, the index into `codes`, `NONE` on water.

    The same layers in the same order as `classify`, laid over the arrays
    instead of read at a point -- so the raster at a cell's centre is the
    classifier's word there, and a test holds the two to it. The shader
    draws by this; nothing of the game is judged by it.
    """
    field = terrain.field_of(constants, planet)
    code = {name: index for index, name in enumerate(codes(constants))}
    out = np.full(field.height.shape, NONE, dtype=np.uint8)
    #: A river cell is land to the classifier (`is_water` is the sea and the lakes).
    todo = (field.water != fields.SEA) & (field.water != fields.LAKE)

    def claim(mask: np.ndarray, name: str) -> None:
        taken = todo & mask
        out[taken] = code[name]
        todo[taken] = False

    if planet in OF_PLANET:
        claim(todo.copy(), OF_PLANET[planet])
        return out
    #: Float64 like the point reading: a cell exactly on the mountain line
    #: must fall the same side of it here and there.
    height = field.height.astype(np.float64)
    claim(field.ice, ICE)
    claim(height >= field.mountain_level, ALPINE)
    azonal = constants[R.BIOME_AZONAL]
    for index, form in enumerate(field.forms):
        if form in azonal:
            claim(field.form == index, azonal[form])
    river_m = field.river_m.astype(np.float64)
    claim(
        (river_m < field.river_cap_m)
        & (river_m <= float(constants[R.TERRAIN_RIVER_REACH_KM]) * METRES_PER_KM),
        FLOODPLAIN,
    )
    sea_m = field.sea_m.astype(np.float64)
    claim(
        (sea_m < field.sea_cap_m) & (sea_m <= _bound(constants, "coast_km") * METRES_PER_KM), COAST
    )
    rain_range = constants[R.SITE_RAIN_RANGE]
    rain = rain_range.min + (rain_range.max - rain_range.min) * (
        field.rain.astype(np.float64) / fields.BYTE
    )
    relief = np.clip(height, 0.0, 1.0)
    claim((rain > _bound(constants, "wet")) & (relief < _bound(constants, "marsh_relief")), MARSH)
    zonal_codes = _zonal_raster(constants, field.temperature_c.astype(np.float64), rain, code)
    out[todo] = zonal_codes[todo]
    #: Land without a class would be drawn as water; the point reading
    #: raises for it, and so does this, rather than paint a quiet sea.
    if (out[todo] == NONE).any():
        raise ValueError("biome.zonal leaves land without a class")
    return out


def _zonal_raster(
    constants: Constants, temperature: np.ndarray, rain: np.ndarray, code: dict[str, int]
) -> np.ndarray:
    """`zonal` over arrays: the same rectangles, the same edge rule, the
    same push to the plane's edge."""
    rows = list(constants[R.BIOME_ZONAL].values())
    top_t = max(float(row["temp"][1]) for row in rows)
    top_r = max(float(row["rain"][1]) for row in rows)
    t = np.clip(temperature, min(float(row["temp"][0]) for row in rows), top_t)
    r = np.clip(rain, min(float(row["rain"][0]) for row in rows), top_r)
    out = np.full(t.shape, NONE, dtype=np.uint8)
    for row in rows:
        (t0, t1), (r0, r1) = (float(v) for v in row["temp"]), (float(v) for v in row["rain"])
        in_t = ((t >= t0) & (t < t1)) | ((t == top_t) & (t1 == top_t))
        in_r = ((r >= r0) & (r < r1)) | ((r == top_r) & (r1 == top_r))
        take = in_t & in_r & (out == NONE)
        out[take] = code[str(row["biome"])]
    return out


def of_node(constants: Constants, node: Node) -> str | None:
    """The node's biome: written on a found node, read off the field for a seeded one."""
    written = (node.properties or {}).get(BIOME)
    if written:
        return str(written)
    point = places.geo_of(node)
    if point is None:
        return None
    return classify(constants, node.planet, *point)


def province_of(node: Node) -> str | None:
    """The province written on a found node, or None: a seeded node and a
    node off the ground have none."""
    written = (node.properties or {}).get(PROVINCE)
    return str(written) if written else None


def signs(node: Node) -> list[str]:
    """The node's biome as a place sign, or nothing.

    The one sign kept as a word rather than a flag, so it does not come out of
    the flag allowlist (`world.public_signs`) and has to be added by hand
    wherever signs are sent -- `look` and the map both. Written only on a
    found node (D-321): a seeded one reads its biome off the field, and the
    map does not ask the field per node.
    """
    written = (node.properties or {}).get(BIOME)
    return [str(written)] if written else []


def name(constants: Constants, biome: str) -> str:
    return str(constants[R.BIOME_NAMES][biome])


def word_of(constants: Constants, node: Node) -> str:
    """How a node is spoken of: by its name, or -- a nameless find -- by its
    biome (D-321).

    A find has no name and is not given one: on the map its kind is the sign
    it wears. But a refusal, a heading and a leg of a walk are sentences, and
    a sentence with a hole in it is a defect -- so they say the kind in words.
    Lives here rather than in `explore` because everything that speaks of a
    node needs it, and two copies of it would let the world and the window
    call one node by two different words.
    """
    if node.name:
        return node.name
    here = of_node(constants, node)
    return name(constants, here) if here else node.key


def reach_m(constants: Constants, biome: str) -> tuple[float, float]:
    """How near and how far one may explore from a node of this biome."""
    span = constants[R.BIOME_REACH_M][biome]
    return float(span["min"]), float(span["max"])


def swing_c(constants: Constants, biome: str) -> float:
    return float(constants[R.BIOME_SWING_C][biome])


def marks(constants: Constants, biome: str) -> dict[str, float]:
    """The share of places of this biome that carry woods, stones, meadow (per cent)."""
    return {key: float(value) for key, value in constants[R.BIOME_MARKS][biome].items()}


def vein_k(constants: Constants, biome: str) -> float:
    return float(constants[R.BIOME_VEIN_K][biome])
