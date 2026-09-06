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

#: The relief of a planet (D-319): one seed for the world, a share of sea and
#: a count of rivers per planet, one mountain line for all, and how much
#: colder the top of the land's rise is than its foot. Within
#: `terrain.river_reach_km` of a river a node has river water, and a node in
#: the mountains bears a vein `terrain.mountain_vein_k` times as often.
TERRAIN_SEED = Num("terrain.seed")
TERRAIN_SEA_SHARE = Table("terrain.sea_share")
TERRAIN_MOUNTAIN_SHARE = Num("terrain.mountain_share")
TERRAIN_RIVERS = Table("terrain.rivers")
TERRAIN_LAPSE_C = Num("terrain.lapse_c")
TERRAIN_RIVER_REACH_KM = Num("terrain.river_reach_km")
TERRAIN_DETAIL_KM = Num("terrain.detail_km")
TERRAIN_DETAIL_AMPLITUDE = Num("terrain.detail_amplitude")
TERRAIN_PEAK_SHARE = Num("terrain.peak_share")
TERRAIN_BASIN_SHARE = Num("terrain.basin_share")
TERRAIN_DETAIL_SEED = Num("terrain.detail_seed")
#: How much of a place's rain is the field's own noise; the rest is water nearby.
TERRAIN_RAIN_NOISE_SHARE = Num("terrain.rain_noise_share")
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
    "TERRAIN_RIVERS",
    "TERRAIN_LAPSE_C",
    "TERRAIN_RIVER_REACH_KM",
    "TERRAIN_DETAIL_KM",
    "TERRAIN_DETAIL_AMPLITUDE",
    "TERRAIN_PEAK_SHARE",
    "TERRAIN_BASIN_SHARE",
    "TERRAIN_DETAIL_SEED",
    "TERRAIN_RAIN_NOISE_SHARE",
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
