"""Реестр констант, от которых зависит движок.

Каждая величина объявляется здесь **один раз** и дальше берётся только отсюда.
Ключей больше, чем сейчас используется кодом, быть не должно: реестр — это не
копия `constants.json`, а список того, на что движок реально опирается.

Порядок разделов повторяет этапы дорожной карты, чтобы было видно, что уже
подключено, а что ещё нет.
"""

from __future__ import annotations

from octoverse.constants.spec import Flag, FormulaRef, Num, Span, Spec, Table, Tiers

# --- Время и тик -----------------------------------------------------------
TIME_TICK = Num("time.tick")
TIME_DAY_TERRA = Num("time.day_terra")

# --- Тело (20-systems/00-character, D-091) ---------------------------------
BODY_STAMINA_MAX = Num("body.stamina_max")
BODY_DRAIN_RATE = Span("body.drain_rate")
BODY_FOOD_RESTORE = Num("body.food_restore")
BODY_DIET_VARIETY_BONUS = Num("body.diet_variety_bonus")
BODY_HIBERNATION_RATE = Num("body.hibernation_rate")
BODY_HIBERNATION_HOME_K = Num("body.hibernation_home_k")

# --- Инвентарь (20-systems/04-items) ---------------------------------------
INVENTORY_CARRY_MASS = Num("inventory.carry_mass")
INVENTORY_CARRY_VOLUME = Num("inventory.carry_volume")

# --- Добыча: жила и соседи (D-099, D-101) ----------------------------------
MINING_IRON_PER_HOUR = Num("mining.iron_per_hour")
MINING_RICH_THRESHOLD = Num("mining.rich_threshold")
MINING_CROWD_RICH_PENALTY = Num("mining.crowd_rich_penalty")
MINING_CROWD_POOR_BONUS = Num("mining.crowd_poor_bonus")
MINING_CROWD_BONUS_CAP = Num("mining.crowd_bonus_cap")
VEIN_DEPLETION_STEP = Num("vein.depletion_step")
VEIN_RICHNESS_DECAY = Num("vein.richness_decay")

# --- Добыча: механика «Свод» (D-143) ---------------------------------------
MINE_ROOF_START = Num("mine.roof_start")
MINE_ROOF_PER_SWING = Num("mine.roof_per_swing")
MINE_ROOF_PER_TIMBER = Num("mine.roof_per_timber")
MINE_ROOF_TIMBER_CAP = Num("mine.roof_timber_cap")
MINE_PACE_K = Num("mine.pace_k")
MINE_SIGN_BANDS = Table("mine.sign_bands")
MINE_SIGN_NOISE = Num("mine.sign_noise")
MINE_COLLAPSE_WEAR = Num("mine.collapse_wear")
MINE_COLLAPSE_WOUND_CHANCE = Num("mine.collapse_wound_chance")

# --- Ранения (D-096) --------------------------------------------------------
WOUND_RECOVERY_HOURS = Span("wound.recovery_hours")
WOUND_STAMINA_PENALTY = Num("wound.stamina_penalty")
WOUND_TREATED_MULTIPLIER = Num("wound.treated_multiplier")

# --- Плата устройства (D-110, D-112, D-113) --------------------------------
POW_SESSION_COMPUTE = Num("pow.session_compute")
POW_COMPUTE_TIME_TARGET = Num("pow.compute_time_target")
POW_COMPUTE_TIME_CAP = Num("pow.compute_time_cap")
POW_MEMORY_PER_SESSION = Num("pow.memory_per_session")
POW_ARGON_ITERATIONS = Num("pow.argon_iterations")
POW_VERIFY_COST = Num("pow.verify_cost")

# --- Износ (D-129) ----------------------------------------------------------
WEAR_TOOL_PER_SESSION = Num("wear.tool_per_session")
WEAR_STATION_PER_BATCH = Num("wear.station_per_batch")
WEAR_GEAR_PER_DAY = Num("wear.gear_per_day")
WEAR_ENVIRONMENT_K = Table("wear.environment_k")

# --- Крафт (D-092, D-133) ---------------------------------------------------
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
HARVEST_RATES = Table("harvest.rates")

# --- Качество (D-058, D-060, D-092) ----------------------------------------
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

# --- Рынок (D-047, D-100, D-127) -------------------------------------------
MARKET_ORDER_LIFETIME = Num("market.order_lifetime")
MARKET_DEFAULT_FEE = Num("market.default_fee")
MARKET_RESERVATION_DEPOSIT = Num("market.reservation_deposit")
MARKET_RESERVATION_PERIOD = Num("market.reservation_period")
MARKET_ORPHAN_DECAY_MULTIPLIER = Num("market.orphan_decay_multiplier")

# --- Порча (D-119) ----------------------------------------------------------
SPOILAGE_FOOD_BASE = Num("spoilage.food_base")
SPOILAGE_COLD_STORAGE_MULTIPLIER = Num("spoilage.cold_storage_multiplier")
SPOILAGE_SALTED_MULTIPLIER = Num("spoilage.salted_multiplier")

# --- Чат локации (D-043) ----------------------------------------------------
CHAT_LEAK_BASE = Num("chat.leak_base")
CHAT_LEAK_PER_PERSON = Num("chat.leak_per_person")
CHAT_LEAK_GROUP_SIZE = Num("chat.leak_group_size")
CHAT_LEAK_QUIET_MULTIPLIER = Num("chat.leak_quiet_multiplier")
CHAT_LEAK_LOCATION_MODIFIER = Table("chat.leak_location_modifier")

# --- Смерть (D-012, D-033) --------------------------------------------------
DEATH_FIRST_BODY_INSTANT = Flag("death.first_body_instant")
DEATH_SALVAGE_RATIO = Num("death.salvage_ratio")


def declared() -> tuple[Spec, ...]:
    """Всё объявленное в этом модуле — источник для проверки на старте."""
    return tuple(
        value
        for name, value in sorted(globals().items())
        if not name.startswith("_") and isinstance(value, Spec)
    )
