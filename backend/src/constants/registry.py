"""Registry of constants the engine depends on.

Every quantity is declared here **once** and from then on taken only from
here. There must be no more keys than the code currently uses: the registry is
not a copy of `constants.json` but a list of what the engine really relies on.

The order of sections follows the roadmap stages so that it is visible what
is already wired up and what is not yet.
"""

from __future__ import annotations

from src.constants.spec import Flag, FormulaRef, Num, Span, Spec, Table, Tiers

# --- Time and tick ----------------------------------------------------------
TIME_TICK = Num("time.tick")
TIME_DAY_TERRA = Num("time.day_terra")

# --- Body (20-systems/00-character, D-091) ----------------------------------
BODY_STAMINA_MAX = Num("body.stamina_max")
BODY_DRAIN_RATE = Span("body.drain_rate")
BODY_FOOD_RESTORE = Num("body.food_restore")
BODY_DIET_VARIETY_BONUS = Num("body.diet_variety_bonus")
BODY_HIBERNATION_RATE = Num("body.hibernation_rate")
BODY_HIBERNATION_HOME_K = Num("body.hibernation_home_k")

# --- Map and transits (D-045, D-089, D-107, D-147) --------------------------
TRAVEL_CITY_STEP = Span("travel.city_step")
TRAVEL_INTRA_CITY = Span("travel.intra_city")
#: Node distance (D-180): the first ring beyond the walls and the growth of each next.
TRAVEL_FRONTIER_STEP = Num("travel.frontier_step")
TRAVEL_FRONTIER_GROWTH = Num("travel.frontier_growth")
TRAVEL_STAMINA_PER_HOUR = Num("travel.stamina_per_hour")
TRANSPORT_STAMINA_K = Num("transport.stamina_k")
# --- Transport (D-107, D-129, D-157) ----------------------------------------
#: Hold capacity and speed -- one layout by one key: two would diverge. The
#: key is the vault's word ("barrow", "wagon"), not the item name.
TRANSPORT_CAPACITY = Table("transport.capacity")
TRANSPORT_SPEED_K = Table("transport.speed_k")
#: From this capacity a vehicle is heavy and needs a paved highway.
TRANSPORT_HEAVY_FROM = Num("transport.heavy_from")
ROAD_TRAIL_MULTIPLIER = Num("road.trail_multiplier")
ROAD_ROAD_MULTIPLIER = Num("road.road_multiplier")
ROAD_PAVED_MULTIPLIER = Num("road.paved_multiplier")
#: Road as work on an edge (D-107, D-158): surface per surface tier, hours
#: to lay, and how much condition an untravelled road loses per day.
ROAD_SURFACE_PER_EDGE = Num("road.surface_per_edge")
ROAD_BUILD_HOURS = Num("road.build_hours")
ROAD_DECAY_RATE = Num("road.decay_rate")

# --- Inventory (20-systems/04-items, D-146) ---------------------------------
INVENTORY_CARRY_MASS = Num("inventory.carry_mass")
INVENTORY_CARRY_VOLUME = Num("inventory.carry_volume")
#: How many kilograms worn gear adds: backpack and exoskeleton raise the limit,
#: clothes and armour take the slot but add nothing to carry.
INVENTORY_CARRY_BONUS = Table("inventory.carry_bonus")
INVENTORY_MASS_BY_KIND = Table("inventory.mass_by_kind")

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
MINE_PACE_K = Num("mine.pace_k")
MINE_SIGN_BANDS = Table("mine.sign_bands")
MINE_SIGN_NOISE = Num("mine.sign_noise")
MINE_COLLAPSE_WEAR = Num("mine.collapse_wear")
MINE_COLLAPSE_WOUND_CHANCE = Num("mine.collapse_wound_chance")

# --- Wounds (D-096) ---------------------------------------------------------
WOUND_RECOVERY_HOURS = Span("wound.recovery_hours")
WOUND_STAMINA_PENALTY = Num("wound.stamina_penalty")
WOUND_TREATED_MULTIPLIER = Num("wound.treated_multiplier")

# --- Device fee (D-110, D-112, D-113) ---------------------------------------
POW_SESSION_COMPUTE = Num("pow.session_compute")
POW_COMPUTE_TIME_TARGET = Num("pow.compute_time_target")
POW_COMPUTE_TIME_CAP = Num("pow.compute_time_cap")
POW_MEMORY_PER_SESSION = Num("pow.memory_per_session")
POW_ARGON_ITERATIONS = Num("pow.argon_iterations")
POW_VERIFY_COST = Num("pow.verify_cost")

# --- Farming (D-118, D-105) -------------------------------------------------
FARM_PLOT_MIN_AREA = Num("farm.plot_min_area")
FARM_PLOW_TIME_PER_M2 = Num("farm.plow_time_per_m2")
FARM_SEED_RATE = Num("farm.seed_rate")
FARM_CARE_TIME_PER_M2 = Num("farm.care_time_per_m2")
FARM_PLOT_OVERHEAD = Num("farm.plot_overhead")
FARM_WATER_PER_M2 = Num("farm.water_per_m2")
FARM_NEGLECT_PENALTY = Num("farm.neglect_penalty")
FARM_SOIL_DEPLETION = Num("farm.soil_depletion")
FARM_FALLOW_RECOVERY = Num("farm.fallow_recovery")

# --- Wear (D-129) -----------------------------------------------------------
WEAR_TOOL_PER_SESSION = Num("wear.tool_per_session")
WEAR_STATION_PER_BATCH = Num("wear.station_per_batch")
WEAR_GEAR_PER_DAY = Num("wear.gear_per_day")
WEAR_ENVIRONMENT_K = Table("wear.environment_k")
#: Transport per transit between nodes, adjusted for hold load (D-157).
WEAR_TRANSPORT_PER_LEG = Num("wear.transport_per_leg")

# --- Craft (D-092, D-133) ---------------------------------------------------
CRAFT_TIME_PER_UNIT = Num("craft.time_per_unit")
CRAFT_STATION_SPEED_K = Span("craft.station_speed_k")
CRAFT_AUTO_SPEED_K = Num("craft.auto_speed_k")
CRAFT_BATCH_MAX = Num("craft.batch_max")
CRAFT_WASTE_SHARE = Num("craft.waste_share")
CRAFT_WASTE_BAD_RATIO = Num("craft.waste_bad_ratio")
CRAFT_RECYCLE_RETURN = Num("craft.recycle_return")
CRAFT_REPAIR_COST_SHARE = Num("craft.repair_cost_share")
CRAFT_INPUT_LABOR_RATIO = Num("craft.input_labor_ratio")
CRAFT_AMOUNT_CAP = Num("craft.amount_cap")
#: Copying a recipe in the Library is paid with the body, not the account (D-148).
CRAFT_COPY_STAMINA = Num("craft.copy_stamina")
HARVEST_RATES = Table("harvest.rates")

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

# --- Market (D-047, D-100, D-127) -------------------------------------------
MARKET_ORDER_LIFETIME = Num("market.order_lifetime")
MARKET_DEFAULT_FEE = Num("market.default_fee")
MARKET_RESERVATION_DEPOSIT = Num("market.reservation_deposit")
MARKET_RESERVATION_PERIOD = Num("market.reservation_period")
MARKET_ORPHAN_DECAY_MULTIPLIER = Num("market.orphan_decay_multiplier")

# --- Cooking (D-119, D-128) -------------------------------------------------
COOK_ROLE_WEIGHTS = Table("cook.role_weights")
COOK_EMPTY_ROLE_PENALTY = Num("cook.empty_role_penalty")
COOK_POT_PORTIONS = Num("cook.pot_portions")
COOK_SPOILAGE_MULTIPLIER = Num("cook.spoilage_multiplier")
COOK_HOT_QUALITY_MIN = Num("cook.hot_quality_min")
COOK_HOT_RESTORE_SHARE = Num("cook.hot_restore_share")
COOK_HOT_DRAIN_REDUCTION = Num("cook.hot_drain_reduction")
COOK_HOT_DURATION = Num("cook.hot_duration")

# --- Food (D-091, D-105, D-121) ---------------------------------------------
FOOD_RESTORE_BY_QUALITY = Span("food.restore_by_quality")
FOOD_VARIETY_WINDOW = Num("food.variety_window")
FOOD_VARIETY_MIN_KINDS = Num("food.variety_min_kinds")

# --- Spoilage (D-119) -------------------------------------------------------
SPOILAGE_FOOD_BASE = Num("spoilage.food_base")
SPOILAGE_COLD_STORAGE_MULTIPLIER = Num("spoilage.cold_storage_multiplier")
SPOILAGE_SALTED_MULTIPLIER = Num("spoilage.salted_multiplier")

# --- Location chat (D-043) --------------------------------------------------
CHAT_LEAK_BASE = Num("chat.leak_base")
CHAT_LEAK_PER_PERSON = Num("chat.leak_per_person")
CHAT_LEAK_CROWD_FREE = Num("chat.leak_crowd_free")
CHAT_LEAK_GROUP_SIZE = Num("chat.leak_group_size")
CHAT_LEAK_GROUP_FREE = Num("chat.leak_group_free")
CHAT_LEAK_QUIET_MULTIPLIER = Num("chat.leak_quiet_multiplier")
CHAT_LEAK_LOCATION_MODIFIER = Table("chat.leak_location_modifier")

# --- Reservation (D-047) ----------------------------------------------------
MARKET_RESERVATION_DEPOSIT = Num("market.reservation_deposit")
MARKET_RESERVATION_PERIOD = Num("market.reservation_period")

# --- Drilling rig (D-115) ---------------------------------------------------
RIG_OUTPUT_PER_HOUR = Num("rig.output_per_hour")
RIG_QUALITY_CAP = Num("rig.quality_cap")
RIG_FUEL_PER_HOUR = Num("rig.fuel_per_hour")
RIG_HOPPER_CAPACITY = Num("rig.hopper_capacity")
RIG_DEPLETION_MULTIPLIER = Num("rig.depletion_multiplier")
RIG_WEAR_PER_DAY = Num("rig.wear_per_day")

# --- Energy (D-071, D-082, D-085) -------------------------------------------
ENERGY_PER_COAL = Num("energy.per_coal")
ENERGY_WATERWHEEL_RATE = Num("energy.waterwheel_rate")
ENERGY_WINDMILL_RATE = Span("energy.windmill_rate")
ENERGY_COAL_PLANT_RATE = Num("energy.coal_plant_rate")
ENERGY_COAL_PLANT_FUEL_DRAW = Num("energy.coal_plant_fuel_draw")
ENERGY_BATTERY_CAPACITY = Num("energy.battery_capacity")
ENERGY_BATTERY_MASS = Num("energy.battery_mass")
ENERGY_BATTERY_SELFDISCHARGE = Num("energy.battery_selfdischarge")
ENERGY_TARIFF_DEFAULT = Num("energy.tariff_default")
ENERGY_AUTO_BENCH_DRAW = Num("energy.auto_bench_draw")
ENERGY_METER_PERIOD = Num("energy.meter_period")

# --- Breeding (D-057, D-067) ------------------------------------------------
FARM_HARVEST_SEED_SHARE = Num("farm.harvest_seed_share")
BREED_INHERIT_DRIFT = FormulaRef("breed.inherit_drift")
BREED_NOVEL_TRAIT_CHANCE = Num("breed.novel_trait_chance")
BREED_HYBRID_DECAY = Num("breed.hybrid_decay")
BREED_GENERATIONS_TO_STABILIZE = Span("breed.generations_to_stabilize")
BREED_DEGRADATION_PER_GEN = Num("breed.degradation_per_gen")
BREED_DISTINCTNESS_THRESHOLD = Num("breed.distinctness_threshold")

# --- Coin (D-016, D-086) ----------------------------------------------------
#: Coin fineness, per mille. There is no debasement mechanic any more: a coin
#: is always of this fineness, and the composition is set by recipe amounts
#: (0.9 refined + 0.1 iron).
COIN_DEFAULT_FINENESS = Num("coin.default_fineness")

# --- Death and body printing (D-012, D-028, D-032, D-033, D-040) ------------
DEATH_FIRST_BODY_INSTANT = Flag("death.first_body_instant")
DEATH_SALVAGE_RATIO = Num("death.salvage_ratio")
DEATH_IRON_COST = Num("death.iron_cost")
DEATH_PRINT_TIME_CITY = Num("death.print_time_city")
DEATH_PRINT_TIME_CAPITAL = Num("death.print_time_capital")
ENERGY_BODY_PRINT = Num("energy.body_print")
MINE_COLLAPSE_DEATH_CHANCE = Num("mine.collapse_death_chance")

# --- Node meter and maintenance (D-135, D-149) ------------------------------
ENERGY_HOME_DRAW_PER_M2 = Num("energy.home_draw_per_m2")

# --- City layout (D-089, D-125) ---------------------------------------------
LAND_AREA_RING1 = Span("land.area_ring1")
#: This many days after the declaration citizenship lapses (D-160). Leaving is
#: free but not instant: otherwise one leaves the city right before a verdict.
CITY_EXIT_DELAY = Num("city.exit_delay")
#: How many hours a citizens' poll runs (D-161). Hours, not minutes: not only
#: those online at the moment of convening take part.
VOTE_DURATION = Num("vote.duration")
#: The shares behind the charter's words "simple majority" and "two thirds".
VOTE_THRESHOLDS = Table("vote.thresholds")

# --- Court (D-095, D-117, D-166) --------------------------------------------
#: The complaint fee: goes to the city treasury rather than vanishing.
JUSTICE_COURT_FEE = Num("justice.court_fee")
#: Limitation period of a complaint. The court is not an archive of grudges.
JUSTICE_CLAIM_WINDOW = Num("justice.claim_window")
#: Imprisonment ceiling in days: the body is held to the node, but not forever.
JUSTICE_PRISON_MAX = Num("justice.prison_max")

# --- Bank (D-030, D-087, D-167) ---------------------------------------------
BANK_BASE_RATE = Num("bank.base_rate")
BANK_TARGET_INFLATION = Num("bank.target_inflation")
BANK_RATE_REACTION_K = Num("bank.rate_reaction_k")
BANK_EMISSION_REACTION_K = Num("bank.emission_reaction_k")
BANK_EMISSION_SHARE_TARGET = Num("bank.emission_share_target")
BANK_RATE_REVIEW_PERIOD = Num("bank.rate_review_period")
BANK_RATE_FLOOR = Num("bank.rate_floor")
BANK_RATE_CAP = Num("bank.rate_cap")
BANK_RATE_STEP_MAX = Num("bank.rate_step_max")
BANK_RISK_PREMIUM = Span("bank.risk_premium")
BANK_UNSECURED_LIMIT = Num("bank.unsecured_limit")
#: Ceiling of the city bank's margin above the key rate (D-175).
BANK_CITY_MARGIN_CAP = Num("bank.city_margin_cap")
#: The city's line with the capital: citizens' debt no higher than this share of turnover (D-175).
BANK_DEBT_TO_TURNOVER_CAP = Num("bank.debt_to_turnover_cap")
#: Credit limit from labour (D-173): shares of turnover and of repaid, the
#: window, the bonus for a record without overdue, the report price and the trust floor.
CREDIT_TURNOVER_SHARE = Num("credit.turnover_share")
CREDIT_REPAID_SHARE = Num("credit.repaid_share")
CREDIT_WINDOW = Num("credit.window")
CREDIT_NO_OVERDUE_BONUS = Num("credit.no_overdue_bonus")
CREDIT_REPORT_PENALTY = Num("credit.report_penalty")
CREDIT_TRUST_FLOOR = Num("credit.trust_floor")
BANK_PRICE_INDEX_WINDOW = Num("bank.price_index_window")
#: The accounting year: there is no year in the world, and "per annum" has nothing to count from
#: (D-167).
BANK_YEAR_DAYS = Num("bank.year_days")
#: Insolvency (D-063, D-168): how many days overdue before withholding, before
#: restriction of freedom, and what share of the balance goes to forced repayment.
DEBT_GRACE_PERIOD = Num("debt.grace_period")
DEBT_PRISON_THRESHOLD = Num("debt.prison_threshold")
DEBT_WORKOFF_RATE = Num("debt.workoff_rate")
#: Reserve ceiling as a share of circulating supply: above it the surplus is burned (D-169).
BANK_RESERVE_CAP = Num("bank.reserve_cap")
#: Inflation at which the collateral lever has done its full stroke (D-170).
BANK_INFLATION_ALARM = Num("bank.inflation_alarm")
#: Handing the rate to the Council of cities (D-172): threshold, corridor and emergency return.
BANK_COUNCIL_HANDOVER_CITIES = Num("bank.council_handover_cities")
BANK_COUNCIL_RATE_DEVIATION = Num("bank.council_rate_deviation")
BANK_COUNCIL_LOCKOUT = Num("bank.council_lockout")
#: By this much a plot gets cheaper with each ring from the bioprinter -- the city centre.
LAND_PRICE_DECAY_PER_RING = Num("land.price_decay_per_ring")

# --- Buildings and construction (D-106, D-125, D-131) -----------------------
#: How much building area one work place takes: a machine or furniture.
BUILD_SLOTS_PER_AREA = Num("build.slots_per_area")
#: Materials of the first durability tier per square metre of floor.
BUILD_MATERIALS_PER_M2 = Table("build.materials_per_m2")
#: Assembly labour: hours per square metre. Construction is work, not a button.
BUILD_LABOR_PER_M2 = Num("build.labor_per_m2")

# --- City economic panel (D-124, D-140) -------------------------------------
#: Summary step. Deliberately slower than the market: instant data would give
#: the authority a trading advantage over its own merchants.
TRADE_REPORT_WINDOW = Num("trade.report_window")
TRADE_REPORT_RETENTION = Num("trade.report_retention")

# --- Customs (D-123) --------------------------------------------------------
#: Duty-free norm window per person: the norm separates household carriage from
#: trade, otherwise the duty first hits the newcomer with a sack of turnips.
TRADE_DUTY_FREE_WINDOW = Num("trade.duty_free_window")
#: The reference price is taken from deals over this period. No deals -- nothing
#: to compute the duty from: first the market, then customs.
TRADE_REFERENCE_PRICE_WINDOW = Num("trade.reference_price_window")

# --- Exploration (D-152, run price -- D-156) --------------------------------
#: A run in untrodden surroundings: minutes. Grows with place depletion from there.
EXPLORE_ATTEMPT_MINUTES = Span("explore.attempt_minutes")
#: Ceiling of run duration, not its length.
EXPLORE_ATTEMPT_HOURS = Num("explore.attempt_hours")
#: This many times longer is each next run from the same node.
EXPLORE_EFFORT_GROWTH = Num("explore.effort_growth")
#: The price of a full-length run; a short one costs by time in the field.
EXPLORE_ATTEMPT_STAMINA = Num("explore.attempt_stamina")
#: The chance in an untrodden place; falls by `find_decay` with each find from
#: here, but not below `find_floor`: the trodden grows poorer, not locked.
EXPLORE_FIND_CHANCE = Num("explore.find_chance")
EXPLORE_FIND_DECAY = Num("explore.find_decay")
EXPLORE_FIND_FLOOR = Num("explore.find_floor")
EXPLORE_VEIN_SHARE = Num("explore.vein_share")
EXPLORE_NODE_AREA = Span("explore.node_area")
#: The transit length to a find is set by the node's distance (D-180), not by a
#: separate exploration quantity: `explore.distance` is abolished.
EXPLORE_VEIN_RICHNESS = Span("explore.vein_richness")
EXPLORE_VEIN_STOCK = Span("explore.vein_stock")

# --- Place properties (D-126) -----------------------------------------------
SITE_TEMP_RANGE = Span("site.temp_range")
SITE_RAIN_RANGE = Span("site.rain_range")
SITE_RIVER_SHARE = Num("site.river_share")
SITE_QUALITY_BUDGET = Num("site.quality_budget")


def declared() -> tuple[Spec, ...]:
    """Everything declared in this module -- the source for the startup check."""
    return tuple(
        value
        for name, value in sorted(globals().items())
        if not name.startswith("_") and isinstance(value, Spec)
    )
