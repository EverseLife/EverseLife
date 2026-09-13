# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The world's own keys: its clock, its sphere, its ground, relief, biomes and sky (D-319, D-321).

The first section split off `registry`, when the registry passed the eight
hundred lines the quality bar allows; it took the map's keys then and the
rest of the world's on the cut by roadmap stage (2026-09-13): the length of
each planet's day, the sphere nodes are laid on, what a place carries, how
its veins are weighted and what it is like (E2), and the relief, trails,
lattice, biomes, season and weather.
Star-imported into the registry, which is the only module that imports it.
"""

from __future__ import annotations

from src.constants.spec import Bands, Book, Num, Shape, Span, Table, Words

#: The four worlds, by the same keys the vault writes them under. Spelled out
#: rather than taken from `models.world.Planet`: the registry sits under the
#: models in the stack (`api -> engine -> models -> constants`), and reaching
#: up for an enum would be the first edge back. A fifth world is a line here
#: and a line there, and the boot then refuses a vault that named only four.
PLANETS = ("terra", "aquatica", "pyroxis", "aurora")

# --- Time and tick ----------------------------------------------------------
TIME_TICK = Num("time.tick")
TIME_DAY_TERRA = Num("time.day_terra")
TIME_DAY_AQUATICA = Num("time.day_aquatica")
TIME_DAY_PYROXIS = Num("time.day_pyroxis")
TIME_DAY_AURORA = Num("time.day_aurora")
#: Diurnal temperature swing around the node's mean, per planet (D-261).
PLANET_TEMP_SWING = Table("planet.temp_swing")

# --- The sphere and its nodes (D-319, D-324) ---------------------------------
#: The yardstick both of a planet's sizes are measured against (D-324): the
#: radius of the Earth. The body in the sky is `PLANET_RADIUS` of it; the
#: land underfoot is this times the root of `PLANET_LAND_AREA_SHARE`.
PLANET_EARTH_RADIUS_KM = Num("planet.earth_radius_km")
#: How much land a world has, as a share of the Earth's surface (D-324). A
#: game convention and nothing else: a world of honest area could not be
#: peopled by every human alive, and the game is about crowding. The surface
#: is a sphere (D-319), so the radius one walks on is the root of this.
PLANET_LAND_AREA_SHARE = Table("planet.land_area_share")
#: The ring a node is seated at from what it was laid beside, and the gap
#: two nodes never stand nearer than -- metres on the tangent plane. Balance
#: since the surface is finite: they decide how much fits on a planet (D-065).
MAP_CITY_STEP_M = Num("map.city_step_m")
MAP_MIN_GAP_M = Num("map.min_gap_m")
#: No node is laid nearer the pole than this latitude: "north up" is not
#: defined there.
MAP_CITY_LAT_MAX = Num("map.city_lat_max")

# --- Ground (D-126, D-151, D-191, D-196; laid at birth since D-319) ----------
#: Once the scout's dice (`explore.*`), the world's now: what a place carries
#: when it is made without a point of the field -- rooms, hand-laid nodes.
GROUND_VEIN_SHARE = Num("ground.vein_share")
#: Forest cover of the world (D-191): the share of places carrying woods.
GROUND_FOREST_SHARE = Num("ground.forest_share")
#: Stony and meadow places (D-196): signs of a place; since D-210 they have
#: no mechanic of their own yet.
GROUND_STONES_SHARE = Num("ground.stones_share")
GROUND_MEADOW_SHARE = Num("ground.meadow_share")
EXPLORE_NODE_AREA = Span("explore.node_area")
GROUND_VEIN_RICHNESS = Span("ground.vein_richness")
GROUND_VEIN_STOCK = Span("ground.vein_stock")
#: An hour's yield of every raw material (D-151): the weight a vein is laid
#: with -- the relative price of everything in the world, in one table.
HARVEST_RATES = Table("harvest.rates")
#: What a planet does to those rates (D-232): Aurora is generous with coal and
#: poor in iron. Multipliers over one table, never a second rarity table.
HARVEST_PLANET_WEIGHTS = Book("harvest.planet_weights")

# --- Place properties (D-126) -----------------------------------------------
SITE_TEMP_RANGE = Span("site.temp_range")
SITE_RAIN_RANGE = Span("site.rain_range")
#: How much of the watering round the rain covers at the top of the scale (D-261).
SITE_RAIN_WATER_OFFSET = Num("site.rain_water_offset")
SITE_RIVER_SHARE = Num("site.river_share")
SITE_QUALITY_BUDGET = Num("site.quality_budget")

# --- Relief, trails, lattice, biomes, season, weather (D-319, D-321, D-334, D-335) ---
#: The field of a planet (D-319, landscape plan wave 2): the vault's pipeline
#: builds it from the planet's own seed, its share of fluid, its own warm and
#: cold ends and the land's rise in metres, and the engine reads the file
#: (`src.field`). Seed and temperature became each planet's own 2026-09-10:
#: one seed over four worlds could not be re-rolled for one of them, and one
#: pair of ends left Pyroxis with ice and Aurora with dunes. The
#: keys the engine still reads itself: the mountain line is the share of the
#: land above it, within `terrain.river_reach_km` of fresh water a node has
#: water, and the field's passport is checked against the seed, the sea,
#: the rise, the grid's step and the algorithm's version at load, so a
#: registry edited without a rebuilt field fails loudly rather than scales
#: the world quietly. A node bears a vein `biome.vein_k` times as often as
#: its biome says (D-321).
TERRAIN_SEED = Table("terrain.seed", keys=PLANETS)
#: The level the world ocean stands at, in the base relief's own units:
#: how much land is left over is a **result** and lives in the field's
#: passport, not here (D-329). It was the share of the surface under
#: water, and the level was then fitted to it -- the ocean answered to
#: the number instead of the land answering to the ocean.
TERRAIN_SEA_LEVEL = Table("terrain.sea_level", keys=PLANETS)
#: What flows on this planet: water or lava. The water raster is one raster on
#: every planet -- a cell is either land or under a fluid, and the engine
#: refuses to walk into either -- but the substance is named and drawn apart.
TERRAIN_FLUID = Words("terrain.fluid", keys=PLANETS, allowed=("water", "lava"))
#: The warm and cold ends of a planet: the equator at sea level and the pole.
TERRAIN_TEMP_RANGE = Bands("terrain.temp_range", keys=PLANETS)
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
#: The eye itself (landscape plan wave 9, §10): how high above the ground one
#: looks from, and how finely the ground between is read. The radius says how
#: far a place can be made out; these two say whether the land is in the way.
MAP_EYE_M = Num("map.eye_m")
MAP_SIGHT_STEP_M = Num("map.sight_step_m")
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
#: explores from them, the swing of their day, the marks and veins they bear.
BIOME_NAMES = Words("biome.names")
BIOME_REACH_M = Bands("biome.reach_m")
BIOME_SWING_C = Table("biome.swing_c")
BIOME_MARKS = Book("biome.marks")
#: The figure the map draws a biome's growth with on the near frames
#: (D-331): a word the client knows, or `none`. The engine never reads it --
#: the client takes it off `/public/constants` with the marks' shares -- and
#: it is declared here so that a misspelt figure is refused at the boot
#: rather than drawn as nothing.
BIOME_FIGURE = Words(
    "biome.figure",
    allowed=("fir", "broadleaf", "palm", "acacia", "bush", "grass", "reed", "moss", "none"),
)
#: The grain the map roughens a biome's ground with on the near frames
#: (D-331 addendum, 2026-09-12): a word the client knows (`grain.GRAIN_KINDS`),
#: read off `/public/constants` like the figure, declared for the same reason.
BIOME_GRAIN = Words(
    "biome.grain",
    allowed=(
        "sand",
        "turf",
        "canopy",
        "blades",
        "dunes",
        "needles",
        "polygons",
        "pools",
        "rubble",
        "scree",
        "cracks",
        "clinker",
        "jungle",
        "tussocks",
        "patches",
        "groves",
    ),
)
BIOME_VEIN_K = Table("biome.vein_k")
#: How a point is sorted into one (landscape plan, wave 4): the zonal table
#: -- rectangles of mean temperature and rain, the vault build checks they
#: tile the plane -- the azonal table of landform -> biome that outranks the
#: climate, and the two bounds of the formless azonal cases (marsh, coast).
BIOME_ZONAL = Shape("biome.zonal")
#: The axes a facet is chosen by (landscape plan wave 7): the wave of its
#: mosaic, the slope that counts as a wall, the reach of the water and the
#: patch a height is ranked in. The rows themselves are `data/facets.yaml`,
#: named things of the vault rather than numbers of the registry.
BIOME_FACET_AXES = Table(
    "biome.facet_axes",
    keys=("wave_m", "slope_full", "wet_km", "patch_km", "soft_edge", "favour_k"),
)
BIOME_AZONAL = Words("biome.azonal")
BIOME_BOUNDS = Table("biome.bounds")
#: Complexes (D-321): how often a find is a scheme of nodes, and the schemes.
COMPLEX_CHANCE = Book("complex.chance")
COMPLEX_SCHEMES = Shape("complex.schemes")

#: The season (D-334): the tilt of a planet's axis to its orbit, degrees --
#: how far the subsolar point walks in latitude over a year -- and the
#: seasonal swing of the mean temperature at the pole, both per planet and
#: for every planet (D-329 item 17: a world without one is refused at the
#: boot, not read as seasonless). The engine reads the swing into the
#: temperature of the moment (`climate.temperature_now`); the rest is the
#: picture's law of snow and ice, declared here so a misspelt number is
#: refused at the boot: the lines the snow and the ice lie below, the band
#: between bare and white, and what a dry cold keeps of the snow.
SEASON_TILT_DEG = Table("season.tilt_deg", keys=PLANETS)
SEASON_SWING_C = Table("season.swing_c", keys=PLANETS)
SEASON_SNOW_C = Num("season.snow_c")
SEASON_SNOW_BAND_C = Num("season.snow_band_c")
SEASON_ICE_C = Num("season.ice_c")
SEASON_SNOW_DRY_RAIN = Num("season.snow_dry_rain")
SEASON_SNOW_DRY_SHARE = Num("season.snow_dry_share")
#: The weather (D-335): the field of cloud and rain the engine reads and
#: the map draws by the same law (`climate.weather_at`, `weatherGlsl.ts`).
#: The cell of the lattice, the wind's drift, how long a system lives, how
#: far the ground's own rain share pulls the cover, and the gates from
#: cover to cloud and to rain.
WEATHER_CELL_KM = Num("weather.cell_km")
WEATHER_WIND_DEG_PER_DAY = Num("weather.wind_deg_per_day")
WEATHER_CHANGE_DAYS = Num("weather.change_days")
WEATHER_WET_BIAS = Num("weather.wet_bias")
WEATHER_CLOUD_FROM = Num("weather.cloud_from")
WEATHER_CLOUD_FULL = Num("weather.cloud_full")
WEATHER_RAIN_FROM = Num("weather.rain_from")
WEATHER_RAIN_FULL = Num("weather.rain_full")
WEATHER_GAIN = Num("weather.gain")
#: D-336: the systems drift by the rain march's belts (`terrain.wind_belts`:
#: the trades' and the westerlies' edges, read by name; the march's own
#: `edge_deg` the engine does not read), the wind turning over the
#: clouds' own edge (`weather.belt_edge_deg`), where it shears and the
#: systems spin at `weather.eddy_turn_deg` a day (item 13).
TERRAIN_WIND_BELTS = Table("terrain.wind_belts", keys=("trade_lat", "westerly_lat"))
WEATHER_BELT_EDGE_DEG = Num("weather.belt_edge_deg")
WEATHER_EDDY_TURN_DEG = Num("weather.eddy_turn_deg")
