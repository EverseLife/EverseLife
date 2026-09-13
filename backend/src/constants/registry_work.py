# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Work's own keys: the vein, the roof, the bench, the machine, the fuel.

A section of `registry`, cut off by roadmap stage when the registry passed the
eight hundred lines the quality bar allows (2026-09-13): mining and its roof
(E1), wear, craft, invention, carriers and quality (E1, E2.5), the drilling
rig and the automats (E2.75), energy (E2.5) and foraging.
Star-imported into the registry, which is the only module that imports it.
"""

from __future__ import annotations

from src.constants.spec import Book, FormulaRef, Num, Span, Table, Tiers, Words

# --- Mining: vein and neighbours (D-099, D-101) -----------------------------
MINING_IRON_PER_HOUR = Num("mining.iron_per_hour")
MINING_RICH_THRESHOLD = Num("mining.rich_threshold")
MINING_CROWD_RICH_PENALTY = Num("mining.crowd_rich_penalty")
MINING_CROWD_POOR_BONUS = Num("mining.crowd_poor_bonus")
MINING_CROWD_BONUS_CAP = Num("mining.crowd_bonus_cap")
VEIN_DEPLETION_STEP = Num("vein.depletion_step")
VEIN_RICHNESS_DECAY = Num("vein.richness_decay")

# --- Mining: the "Roof" mechanic (D-143) ------------------------------------
MINE_ROOF_START = Num("mine.roof_start")
MINE_ROOF_PER_SWING = Num("mine.roof_per_swing")
MINE_ROOF_PER_TIMBER = Num("mine.roof_per_timber")
MINE_ROOF_TIMBER_CAP = Num("mine.roof_timber_cap")
#: The working's own measure, hidden (D-302): how far its starting roof and
#: its timber ceiling stand from the computed ones, either way.
MINE_ROOF_SPREAD = Num("mine.roof_spread")
#: What a cave-in leaves behind (D-301): swings without yield, on a vein of
#: ordinary richness.
MINE_RUBBLE_SWINGS = Num("mine.rubble_swings")
MINE_PACE_K = Num("mine.pace_k")
MINE_SIGN_BANDS = Table("mine.sign_bands")
MINE_SIGN_NOISE = Num("mine.sign_noise")
MINE_COLLAPSE_WEAR = Num("mine.collapse_wear")
MINE_COLLAPSE_WOUND_CHANCE = Num("mine.collapse_wound_chance")
MINE_COLLAPSES_SURVIVED = Num("mine.collapses_survived")

# --- Wear (D-129) -----------------------------------------------------------
WEAR_TOOL_PER_SESSION = Num("wear.tool_per_session")
#: The fifth stream (D-309): a tool in a batch's requirements wears by the hours
#: worked -- felling has no machine at all, and until this the axe was eternal.
WEAR_TOOL_PER_HOUR = Num("wear.tool_per_hour")
WEAR_STATION_PER_BATCH = Num("wear.station_per_batch")
WEAR_GEAR_PER_DAY = Num("wear.gear_per_day")
WEAR_ENVIRONMENT_K = Table("wear.environment_k")
#: Transport per transit between nodes, adjusted for hold load (D-157).
WEAR_TRANSPORT_PER_LEG = Num("wear.transport_per_leg")

# --- Craft (D-092, D-133) ---------------------------------------------------
CRAFT_TIME_PER_UNIT = Num("craft.time_per_unit")
CRAFT_STATION_SPEED_K = Span("craft.station_speed_k")
CRAFT_BATCH_MAX = Num("craft.batch_max")
CRAFT_WASTE_SHARE = Num("craft.waste_share")
CRAFT_WASTE_BAD_RATIO = Num("craft.waste_bad_ratio")
CRAFT_RECYCLE_RETURN = Num("craft.recycle_return")
CRAFT_REPAIR_COST_SHARE = Num("craft.repair_cost_share")
CRAFT_INPUT_LABOR_RATIO = Num("craft.input_labor_ratio")
CRAFT_AMOUNT_CAP = Num("craft.amount_cap")
#: Copying a recipe in the Library is paid with the body, not the account (D-148).
CRAFT_COPY_STAMINA = Num("craft.copy_stamina")
#: What a machine on electricity draws per hour of a manual batch (D-269).
CRAFT_POWERED_ENERGY_PER_HOUR = Num("craft.powered_energy_per_hour")

# --- Invention (D-064, D-209) -----------------------------------------------
#: How many kinds of things may go into one attempt: without a cap the search
#: space is not surveyable and guessing turns into a lottery.
INVENT_MAX_INGREDIENTS = Num("invent.max_ingredients")
#: What share of the laid-out materials a failed attempt burns: a random
#: share within this span, rolled per kind of thing laid out.
INVENT_MATERIAL_LOSS = Span("invent.material_loss")

# --- Knowledge carrier (D-209) ------------------------------------------------
#: Writing time by the blank's quality: `max` seconds at quality 0, `min` at 100.
CARRIER_WRITE_SECONDS = Span("carrier.write_seconds")
#: Quality the memory loses per write and per wipe. At zero the blank is dead.
CARRIER_WRITE_WEAR = Num("carrier.write_wear")
CARRIER_WIPE_WEAR = Num("carrier.wipe_wear")

# --- Quality (D-058, D-060, D-092) ------------------------------------------
QUALITY_SCALE = Span("quality.scale")
QUALITY_TIERS = Tiers("quality.tiers")
QUALITY_DURABILITY_FACTOR = FormulaRef("quality.durability_factor")
QUALITY_SPREAD_GOOD_RATIO = Num("quality.spread_good_ratio")
QUALITY_SPREAD_BAD_RATIO = Num("quality.spread_bad_ratio")
QUALITY_MATERIAL_WEIGHT = Num("quality.material_weight")
QUALITY_RATIO_WEIGHT = Num("quality.ratio_weight")
QUALITY_HAND_CRAFT_BONUS = Num("quality.hand_craft_bonus")
QUALITY_REPAIR_CEILING_LOSS = Num("quality.repair_ceiling_loss")
QUALITY_RECYCLE_CARRYOVER = Num("quality.recycle_carryover")

# --- Drilling rig (D-115) ---------------------------------------------------
RIG_OUTPUT_PER_HOUR = Num("rig.output_per_hour")
RIG_QUALITY_CAP = Num("rig.quality_cap")
RIG_FUEL_PER_HOUR = Num("rig.fuel_per_hour")
RIG_HOPPER_CAPACITY = Num("rig.hopper_capacity")
RIG_DEPLETION_MULTIPLIER = Num("rig.depletion_multiplier")
RIG_WEAR_PER_DAY = Num("rig.wear_per_day")

# --- Automats (D-253) --------------------------------------------------------
#: The automat family (D-253): a station that works without the player,
#: burning lubricant and pool energy by the hour. One set of knobs for the
#: whole family; which station each automat stands in for is `auto.covers`.
AUTO_ENERGY_PER_HOUR = Num("auto.energy_per_hour")
AUTO_LUBE_PER_HOUR = Num("auto.lube_per_hour")
AUTO_WEAR_PER_DAY = Num("auto.wear_per_day")
AUTO_QUALITY_CAP = Num("auto.quality_cap")
AUTO_SPEED_SHARE = Num("auto.speed_share")
#: station -> {automat: 1}. Both sides are dict keys so the D-251 rename
#: pass translates them; the one means membership, not a number.
AUTO_COVERS = Book("auto.covers")
#: The pyroxite tier is barred until its own station exists (OQ-106).
AUTO_BARRED_INPUTS = Table("auto.barred_inputs")

#: The field automaton (D-120, D-339): a programme of commands over plots.
#: Lubricant is the family's (`auto.lube_per_hour`); the rest is its own.
AGRO_YIELD_SHARE = Num("agro.yield_share")
AGRO_QUALITY_CAP = Num("agro.quality_cap")
AGRO_PLOT_MIN_AREA = Num("agro.plot_min_area")
AGRO_PLOTS_MAX = Num("agro.plots_max")
AGRO_PROGRAM_STEPS = Num("agro.program_steps")
AGRO_ENERGY_PER_HOUR = Num("agro.energy_per_hour")
AGRO_WEAR_PER_DAY = Num("agro.wear_per_day")
AGRO_MOISTURE_BAND = Num("agro.moisture_band")
#: The longest fallow or weeding period a programme line may name, Terran days.
AGRO_DAYS_MAX = Num("agro.days_max")

# --- Energy (D-071, D-082, D-085) -------------------------------------------
#: Energy per unit of every burnable material, keyed by name (D-215): the old
#: `energy.per_coal` generalized -- built by the vault from material `fuel` fields.
ENERGY_FUEL_ENERGY = Table("energy.fuel_energy")
ENERGY_WATERWHEEL_RATE = Num("energy.waterwheel_rate")
ENERGY_WINDMILL_RATE = Span("energy.windmill_rate")
ENERGY_COAL_PLANT_RATE = Num("energy.coal_plant_rate")
ENERGY_COAL_PLANT_FUEL_DRAW = Num("energy.coal_plant_fuel_draw")
#: Generators that need neither river, wind nor fuel (D-288): the panel and
#: the isotope generator, per hour. Off the grid only -- aboard a hull, on
#: airless ground -- into the batteries within reach; a city's pool they
#: never feed (`battery.tick_offgrid`).
ENERGY_SOLAR_RATE = Num("energy.solar_rate")
ENERGY_ISOTOPE_RATE = Num("energy.isotope_rate")
ENERGY_BATTERY_CAPACITY = Num("energy.battery_capacity")
ENERGY_BATTERY_MASS = Num("energy.battery_mass")
ENERGY_BATTERY_SELFDISCHARGE = Num("energy.battery_selfdischarge")
ENERGY_TARIFF_DEFAULT = Num("energy.tariff_default")
ENERGY_METER_PERIOD = Num("energy.meter_period")

# --- Foraging (D-210) -------------------------------------------------------
#: Below this much empty land -- plot minus the building footprint -- there is
#: nowhere to forage, and the window is not shown at all.
FORAGE_MIN_AREA = Num("forage.min_area")
#: The empty area the paces in `forage.finds` are stated for.
FORAGE_REFERENCE_AREA = Num("forage.reference_area")
#: Finds per hour per reference area, by thing. The sum sets the pace of the
#: search, the share of the sum sets what turns up: one number per thing.
FORAGE_FINDS = Table("forage.finds")
#: How many units one find brings, by thing. Same keys as `forage.finds`.
FORAGE_HANDFUL = Table("forage.handful")
#: The place mark a find lies under (D-254): a subset of `forage.finds`, and a
#: thing missing from it lies everywhere. The land's marks decide what a walk
#: over it can turn up at all -- stone on stony ground, water by the river.
FORAGE_PLACE = Words("forage.place")
#: A search never goes faster than this many seconds, however much land.
FORAGE_SEARCH_FLOOR = Num("forage.search_floor")
#: Spread of one search's length around the computed one.
FORAGE_SEARCH_JITTER = Span("forage.search_jitter")
#: Stamina per search, found or passed; a body with none does not search.
FORAGE_SEARCH_STAMINA = Num("forage.search_stamina")
#: The quality of what lies on the ground: triangular, its peak mid-span.
FORAGE_QUALITY = Span("forage.quality")
