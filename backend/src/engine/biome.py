# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Which biome a point of the field is, and what the biome says about a node (D-321).

The hidden map of a planet is the relief and the climate (`terrain`); the biome
is their reading at a point, sorted into a dozen classes the vault names:
coast, floodplain, forest, steppe, desert, taiga, tundra, marsh, foothills,
alpine -- and one for the whole of Aurora (ice) and Pyroxis (cinder). The class
decides what exploration may do from a node (`biome.reach_m`), what a found
node looks like (`biome.marks`, `biome.vein_k`), how hot its day and cold its
night (`biome.swing_c`) and what it is called. The bounds between classes are
the vault's (`biome.bounds`); nothing here is a number of its own.
"""

from __future__ import annotations

import math

from src import globe
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

#: Planets whose whole surface is one biome: the ice of Aurora (D-232), the
#: black fields of Pyroxis (D-233). Their relief still decides water and rock.
OF_PLANET: dict[Planet, str] = {Planet.AURORA: ICE, Planet.PYROXIS: CINDER}


def _bound(constants: Constants, name: str) -> float:
    return float(constants[R.BIOME_BOUNDS][name])


def _near_sea(constants: Constants, planet: Planet, lat: float, lon: float) -> bool:
    field = terrain.field_of(constants, planet)
    radius = globe.radius_m(constants, planet)
    reach = _bound(constants, "coast_km") * METRES_PER_KM
    for step in range(globe.COMPASS_POINTS):
        angle = math.tau * step / globe.COMPASS_POINTS
        there = globe.offset(radius, (lat, lon), reach * math.cos(angle), reach * math.sin(angle))
        if field.is_sea(*there):
            return True
    return False


def _near_river(constants: Constants, planet: Planet, lat: float, lon: float) -> bool:
    field = terrain.field_of(constants, planet)
    return field.river_distance_deg(lat, lon) <= terrain.river_reach_deg(constants, planet, lat)


def classify(constants: Constants, planet: Planet, lat: float, lon: float) -> str | None:
    """The biome at a point, or nothing where there is water.

    Read top-down the way a geographer would: rock before soil, water's edge
    before the open land, then cold, then dryness, then wetness -- and the
    forest is what is left when nothing else claims the place.
    """
    field = terrain.field_of(constants, planet)
    if field.is_water(lat, lon):
        return None
    if planet in OF_PLANET:
        return OF_PLANET[planet]
    if field.is_mountain(lat, lon):
        return ALPINE
    if field.relief(lat, lon) >= _bound(constants, "hills_relief"):
        return FOOTHILLS
    if _near_river(constants, planet, lat, lon):
        return FLOODPLAIN
    if _near_sea(constants, planet, lat, lon):
        return COAST
    temperature, rain = terrain.climate_at(constants, planet, lat, lon)
    if temperature < _bound(constants, "cold_c"):
        return TUNDRA
    if temperature < _bound(constants, "cool_c"):
        return TAIGA
    if rain < _bound(constants, "dry"):
        return DESERT if abs(lat) <= _bound(constants, "desert_lat") else STEPPE
    if rain > _bound(constants, "wet") and field.relief(lat, lon) < _bound(
        constants, "marsh_relief"
    ):
        return MARSH
    return FOREST


def of_node(constants: Constants, node: Node) -> str | None:
    """The node's biome: written on a found node, read off the field for a seeded one."""
    written = (node.properties or {}).get(BIOME)
    if written:
        return str(written)
    point = places.geo_of(node)
    if point is None:
        return None
    return classify(constants, node.planet, *point)


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
