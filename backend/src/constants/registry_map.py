# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The map's own keys: the relief, the trails, the lattice, the biomes (D-319, D-321).

The other half of `registry`, split off when the registry passed the eight
hundred lines the quality bar allows. Imported into the registry by a star
import so that `declared()` sees these specs among the rest and the startup
check covers them; nothing else imports this module directly.
"""

from __future__ import annotations

from src.constants.spec import Bands, Book, Num, Shape, Table, Words

#: The field of a planet (D-319, landscape plan wave 2): the vault's pipeline
#: builds it from one seed for the world, a share of sea per planet and the
#: land's rise in metres, and the engine reads the file (`src.field`). The
#: keys the engine still reads itself: the mountain line is the share of the
#: land above it, within `terrain.river_reach_km` of fresh water a node has
#: water, and the field's passport is checked against the seed, the sea,
#: the rise, the grid's step and the algorithm's version at load, so a
#: registry edited without a rebuilt field fails loudly rather than scales
#: the world quietly. A node bears a vein `biome.vein_k` times as often as
#: its biome says (D-321).
TERRAIN_SEED = Num("terrain.seed")
TERRAIN_SEA_SHARE = Table("terrain.sea_share")
TERRAIN_MOUNTAIN_SHARE = Num("terrain.mountain_share")
TERRAIN_LAPSE_C = Num("terrain.lapse_c")
TERRAIN_RELIEF_M = Num("terrain.relief_m")
TERRAIN_STEP_M = Num("terrain.step_m")
TERRAIN_VERSION = Num("terrain.version")
TERRAIN_RIVER_REACH_KM = Num("terrain.river_reach_km")
#: Read by the tests of the climate's range: how far the continent's depth
#: and the local weather may pull a temperature under the cold end.
TERRAIN_CONTINENTAL_C = Num("terrain.continental_c")
TERRAIN_CLIMATE_NOISE_C = Num("terrain.climate_noise_c")
#: A trail is worn in by feet, never laid (D-319): this many arrivals over
#: an untrodden edge make it a trail, below the lower mark it grows over
#: again, and the daily tick takes this much wear off every edge. Two marks,
#: not one, so an edge on the line does not flicker.
PATH_WEAR_THRESHOLD = Num("path.wear_threshold")
PATH_FADE_THRESHOLD = Num("path.fade_threshold")
PATH_FADE_PER_DAY = Num("path.fade_per_day")
#: The lattice of a planet (D-321): the point a scout aims at is pressed to
#: the nearest cell, and the cell is the found node's key -- one node for
#: everybody (D-237) without a row laid in advance.
MAP_LATTICE_M = Num("map.lattice_m")
#: Memory instead of fog (D-319 п. 6-7): how far the eye reaches over the
#: globe, how many places an identity keeps, and how old the public map is.
MAP_SIGHT_KM = Num("map.sight_km")
#: Read by the client through `/public/constants`: the floor of the surface
#: band of the map (D-319). Declared so that the vault's key is checked at
#: bootstrap like every other, though the server itself does not read it.
MAP_APPROACH_KM = Num("map.approach_km")
MAP_MEMORY_PLACES = Num("map.memory_places")
#: The map as a thing (D-319 item 6): what drawing one's memory onto a sheet
#: costs in stamina -- a copy's price, its own key.
MAP_DRAW_STAMINA = Num("map.draw_stamina")
MAP_PUBLIC_DELAY_DAYS = Num("map.public_delay_days")
#: The share of the free radius a find fills (D-321): a long leap lands on
#: wide ground, and the exclusion round a node is its own circle.
EXPLORE_FILL_SHARE = Num("explore.fill_share")
#: How far round the aim the surface is consulted (D-321): the nodes and
#: ways within it decide the room and the crossings; beyond it the ground
#: is taken as empty. Far wider than any reach, so nothing that matters is
#: outside it -- and a window at all, so an aim does not read the planet.
EXPLORE_WINDOW_KM = Num("explore.window_km")
#: Biomes (D-321): the classes of the field, their names, how near and far one
#: explores from them, the swing of their day, the marks and veins they bear,
#: and the bounds that sort a point into one.
BIOME_NAMES = Words("biome.names")
BIOME_REACH_M = Bands("biome.reach_m")
BIOME_SWING_C = Table("biome.swing_c")
BIOME_MARKS = Book("biome.marks")
BIOME_VEIN_K = Table("biome.vein_k")
BIOME_BOUNDS = Table("biome.bounds")
#: Complexes (D-321): how often a find is a scheme of nodes, and the schemes.
COMPLEX_CHANCE = Book("complex.chance")
COMPLEX_SCHEMES = Shape("complex.schemes")

__all__ = [
    "TERRAIN_SEED",
    "TERRAIN_SEA_SHARE",
    "TERRAIN_MOUNTAIN_SHARE",
    "TERRAIN_LAPSE_C",
    "TERRAIN_RELIEF_M",
    "TERRAIN_STEP_M",
    "TERRAIN_VERSION",
    "TERRAIN_RIVER_REACH_KM",
    "TERRAIN_CONTINENTAL_C",
    "TERRAIN_CLIMATE_NOISE_C",
    "PATH_WEAR_THRESHOLD",
    "PATH_FADE_THRESHOLD",
    "PATH_FADE_PER_DAY",
    "MAP_APPROACH_KM",
    "MAP_LATTICE_M",
    "MAP_SIGHT_KM",
    "MAP_MEMORY_PLACES",
    "MAP_DRAW_STAMINA",
    "MAP_PUBLIC_DELAY_DAYS",
    "EXPLORE_FILL_SHARE",
    "EXPLORE_WINDOW_KM",
    "BIOME_NAMES",
    "BIOME_REACH_M",
    "BIOME_SWING_C",
    "BIOME_MARKS",
    "BIOME_VEIN_K",
    "BIOME_BOUNDS",
    "COMPLEX_CHANCE",
    "COMPLEX_SCHEMES",
]
