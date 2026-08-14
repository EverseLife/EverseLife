"""Реестр констант, от которых зависит движок.

Каждая величина объявляется здесь **один раз** и дальше берётся только отсюда.
Ключей больше, чем сейчас используется кодом, быть не должно: реестр — это не
копия `constants.json`, а список того, на что движок реально опирается.

Порядок разделов повторяет этапы дорожной карты, чтобы было видно, что уже
подключено, а что ещё нет.
"""

from __future__ import annotations

from src.constants.spec import Flag, FormulaRef, Num, Span, Spec, Table, Tiers

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

# --- Карта и переходы (D-045, D-089, D-107, D-147) -------------------------
TRAVEL_CITY_STEP = Span("travel.city_step")
TRAVEL_INTRA_CITY = Span("travel.intra_city")
TRAVEL_INTER_NODE = Span("travel.inter_node")
TRAVEL_STAMINA_PER_HOUR = Num("travel.stamina_per_hour")
TRANSPORT_STAMINA_K = Num("transport.stamina_k")
# --- Транспорт (D-107, D-129, D-157) ---------------------------------------
#: Грузоподъёмность трюма и скорость — одна раскладка по одному ключу: две
#: разошлись бы. Ключ — слово вольта («тачка», «повозка»), а не имя предмета.
TRANSPORT_CAPACITY = Table("transport.capacity")
TRANSPORT_SPEED_K = Table("transport.speed_k")
#: От этой грузоподъёмности транспорт тяжёлый и требует мощёного тракта.
TRANSPORT_HEAVY_FROM = Num("transport.heavy_from")
ROAD_TRAIL_MULTIPLIER = Num("road.trail_multiplier")
ROAD_ROAD_MULTIPLIER = Num("road.road_multiplier")
ROAD_PAVED_MULTIPLIER = Num("road.paved_multiplier")
#: Дорога как работа на ребре (D-107, D-158): полотна на ступень покрытия,
#: часов на укладку и сколько состояния теряет непроезженная дорога за сутки.
ROAD_SURFACE_PER_EDGE = Num("road.surface_per_edge")
ROAD_BUILD_HOURS = Num("road.build_hours")
ROAD_DECAY_RATE = Num("road.decay_rate")

# --- Инвентарь (20-systems/04-items, D-146) --------------------------------
INVENTORY_CARRY_MASS = Num("inventory.carry_mass")
INVENTORY_CARRY_VOLUME = Num("inventory.carry_volume")
#: Сколько килограммов добавляет надетое: рюкзак и экзоскелет поднимают предел,
#: одежда и броня слот занимают, но переносимого не добавляют.
INVENTORY_CARRY_BONUS = Table("inventory.carry_bonus")
INVENTORY_MASS_BY_KIND = Table("inventory.mass_by_kind")

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

# --- Земледелие (D-118, D-105) ----------------------------------------------
FARM_PLOT_MIN_AREA = Num("farm.plot_min_area")
FARM_PLOW_TIME_PER_M2 = Num("farm.plow_time_per_m2")
FARM_SEED_RATE = Num("farm.seed_rate")
FARM_CARE_TIME_PER_M2 = Num("farm.care_time_per_m2")
FARM_PLOT_OVERHEAD = Num("farm.plot_overhead")
FARM_WATER_PER_M2 = Num("farm.water_per_m2")
FARM_NEGLECT_PENALTY = Num("farm.neglect_penalty")
FARM_SOIL_DEPLETION = Num("farm.soil_depletion")
FARM_FALLOW_RECOVERY = Num("farm.fallow_recovery")

# --- Износ (D-129) ----------------------------------------------------------
WEAR_TOOL_PER_SESSION = Num("wear.tool_per_session")
WEAR_STATION_PER_BATCH = Num("wear.station_per_batch")
WEAR_GEAR_PER_DAY = Num("wear.gear_per_day")
WEAR_ENVIRONMENT_K = Table("wear.environment_k")
#: Транспорт за переход между узлами, с поправкой на загрузку трюма (D-157).
WEAR_TRANSPORT_PER_LEG = Num("wear.transport_per_leg")

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
#: Копирование рецепта в Библиотеке платится телом, а не счётом (D-148).
CRAFT_COPY_STAMINA = Num("craft.copy_stamina")
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

# --- Готовка (D-119, D-128) --------------------------------------------------
COOK_ROLE_WEIGHTS = Table("cook.role_weights")
COOK_EMPTY_ROLE_PENALTY = Num("cook.empty_role_penalty")
COOK_POT_PORTIONS = Num("cook.pot_portions")
COOK_SPOILAGE_MULTIPLIER = Num("cook.spoilage_multiplier")
COOK_HOT_QUALITY_MIN = Num("cook.hot_quality_min")
COOK_HOT_RESTORE_SHARE = Num("cook.hot_restore_share")
COOK_HOT_DRAIN_REDUCTION = Num("cook.hot_drain_reduction")
COOK_HOT_DURATION = Num("cook.hot_duration")

# --- Еда (D-091, D-105, D-121) -----------------------------------------------
FOOD_RESTORE_BY_QUALITY = Span("food.restore_by_quality")
FOOD_VARIETY_WINDOW = Num("food.variety_window")
FOOD_VARIETY_MIN_KINDS = Num("food.variety_min_kinds")

# --- Порча (D-119) ----------------------------------------------------------
SPOILAGE_FOOD_BASE = Num("spoilage.food_base")
SPOILAGE_COLD_STORAGE_MULTIPLIER = Num("spoilage.cold_storage_multiplier")
SPOILAGE_SALTED_MULTIPLIER = Num("spoilage.salted_multiplier")

# --- Чат локации (D-043) ----------------------------------------------------
CHAT_LEAK_BASE = Num("chat.leak_base")
CHAT_LEAK_PER_PERSON = Num("chat.leak_per_person")
CHAT_LEAK_CROWD_FREE = Num("chat.leak_crowd_free")
CHAT_LEAK_GROUP_SIZE = Num("chat.leak_group_size")
CHAT_LEAK_GROUP_FREE = Num("chat.leak_group_free")
CHAT_LEAK_QUIET_MULTIPLIER = Num("chat.leak_quiet_multiplier")
CHAT_LEAK_LOCATION_MODIFIER = Table("chat.leak_location_modifier")

# --- Бронь (D-047) ----------------------------------------------------------
MARKET_RESERVATION_DEPOSIT = Num("market.reservation_deposit")
MARKET_RESERVATION_PERIOD = Num("market.reservation_period")

# --- Буровая (D-115) --------------------------------------------------------
RIG_OUTPUT_PER_HOUR = Num("rig.output_per_hour")
RIG_QUALITY_CAP = Num("rig.quality_cap")
RIG_FUEL_PER_HOUR = Num("rig.fuel_per_hour")
RIG_HOPPER_CAPACITY = Num("rig.hopper_capacity")
RIG_DEPLETION_MULTIPLIER = Num("rig.depletion_multiplier")
RIG_WEAR_PER_DAY = Num("rig.wear_per_day")

# --- Энергия (D-071, D-082, D-085) ------------------------------------------
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

# --- Селекция (D-057, D-067) ------------------------------------------------
FARM_HARVEST_SEED_SHARE = Num("farm.harvest_seed_share")
BREED_INHERIT_DRIFT = FormulaRef("breed.inherit_drift")
BREED_NOVEL_TRAIT_CHANCE = Num("breed.novel_trait_chance")
BREED_HYBRID_DECAY = Num("breed.hybrid_decay")
BREED_GENERATIONS_TO_STABILIZE = Span("breed.generations_to_stabilize")
BREED_DEGRADATION_PER_GEN = Num("breed.degradation_per_gen")
BREED_DISTINCTNESS_THRESHOLD = Num("breed.distinctness_threshold")

# --- Монета (D-016, D-086) --------------------------------------------------
#: Проба монеты, ‰. Механики занижения больше нет: монета всегда этой пробы,
#: а состав задан количествами рецепта (0.9 аффинажа + 0.1 железа).
COIN_DEFAULT_FINENESS = Num("coin.default_fineness")

# --- Смерть и печать тела (D-012, D-028, D-032, D-033, D-040) ---------------
DEATH_FIRST_BODY_INSTANT = Flag("death.first_body_instant")
DEATH_SALVAGE_RATIO = Num("death.salvage_ratio")
DEATH_IRON_COST = Num("death.iron_cost")
DEATH_PRINT_TIME_CITY = Num("death.print_time_city")
DEATH_PRINT_TIME_CAPITAL = Num("death.print_time_capital")
ENERGY_BODY_PRINT = Num("energy.body_print")
MINE_COLLAPSE_DEATH_CHANCE = Num("mine.collapse_death_chance")

# --- Счётчик и содержание узла (D-135, D-149) -------------------------------
ENERGY_HOME_DRAW_PER_M2 = Num("energy.home_draw_per_m2")

# --- Планировка города (D-089, D-125) ---------------------------------------
LAND_AREA_RING1 = Span("land.area_ring1")
#: Через столько суток после заявления спадает гражданство (D-160). Выход
#: свободен, но не мгновенен: иначе из города выходят перед самым приговором.
CITY_EXIT_DELAY = Num("city.exit_delay")
#: Сколько часов идёт голосование граждан (D-161). Часы, а не минуты:
#: участвуют не только те, кто онлайн в момент созыва.
VOTE_DURATION = Num("vote.duration")
#: Доли, стоящие за словами устава «простое большинство» и «две трети».
VOTE_THRESHOLDS = Table("vote.thresholds")

# --- Суд (D-095, D-117, D-166) ----------------------------------------------
#: Пошлина за жалобу: идёт в казну города, а не исчезает.
JUSTICE_COURT_FEE = Num("justice.court_fee")
#: Срок давности жалобы. Суд — не архив обид.
JUSTICE_CLAIM_WINDOW = Num("justice.claim_window")
#: Потолок заключения в сутках: тело держат узлом, но не вечно.
JUSTICE_PRISON_MAX = Num("justice.prison_max")

# --- Банк (D-030, D-087, D-167) ---------------------------------------------
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
#: Потолок маржи городского банка сверх ключевой (D-175).
BANK_CITY_MARGIN_CAP = Num("bank.city_margin_cap")
#: Линия города перед столицей: долг граждан не выше этой доли оборота (D-175).
BANK_DEBT_TO_TURNOVER_CAP = Num("bank.debt_to_turnover_cap")
#: Кредитный лимит из труда (D-173): доли оборота и возвращённого, окно,
#: прибавка за стаж без просрочек, цена репорта и пол доверия.
CREDIT_TURNOVER_SHARE = Num("credit.turnover_share")
CREDIT_REPAID_SHARE = Num("credit.repaid_share")
CREDIT_WINDOW = Num("credit.window")
CREDIT_NO_OVERDUE_BONUS = Num("credit.no_overdue_bonus")
CREDIT_REPORT_PENALTY = Num("credit.report_penalty")
CREDIT_TRUST_FLOOR = Num("credit.trust_floor")
BANK_PRICE_INDEX_WINDOW = Num("bank.price_index_window")
#: Расчётный год: года в мире нет, и «годовых» не от чего считать (D-167).
BANK_YEAR_DAYS = Num("bank.year_days")
#: Несостоятельность (D-063, D-168): сколько суток просрочки до удержания, до
#: ограничения свободы и какая доля остатка уходит в погашение принудительно.
DEBT_GRACE_PERIOD = Num("debt.grace_period")
DEBT_PRISON_THRESHOLD = Num("debt.prison_threshold")
DEBT_WORKOFF_RATE = Num("debt.workoff_rate")
#: Потолок резерва долей оборотной массы: сверх него излишек сжигается (D-169).
BANK_RESERVE_CAP = Num("bank.reserve_cap")
#: Инфляция, при которой рычаг залога отработал полный ход (D-170).
BANK_INFLATION_ALARM = Num("bank.inflation_alarm")
#: Передача ставки Совету городов (D-172): порог, коридор и аварийный возврат.
BANK_COUNCIL_HANDOVER_CITIES = Num("bank.council_handover_cities")
BANK_COUNCIL_RATE_DEVIATION = Num("bank.council_rate_deviation")
BANK_COUNCIL_LOCKOUT = Num("bank.council_lockout")
#: На столько дешевеет участок с каждым кольцом от биопринтера — центра города.
LAND_PRICE_DECAY_PER_RING = Num("land.price_decay_per_ring")

# --- Здания и стройка (D-106, D-125, D-131) ----------------------------------
#: Сколько площади здания занимает одно рабочее место: станок либо мебель.
BUILD_SLOTS_PER_AREA = Num("build.slots_per_area")
#: Материалы первой ступени прочности на квадратный метр застройки.
BUILD_MATERIALS_PER_M2 = Table("build.materials_per_m2")
#: Труд сборки: часов на квадратный метр. Стройка — работа, а не кнопка.
BUILD_LABOR_PER_M2 = Num("build.labor_per_m2")

# --- Экономическая панель города (D-124, D-140) -----------------------------
#: Шаг сводки. Медленнее рынка нарочно: мгновенные данные дали бы власти
#: торговое преимущество перед собственными купцами.
TRADE_REPORT_WINDOW = Num("trade.report_window")
TRADE_REPORT_RETENTION = Num("trade.report_retention")

# --- Таможня (D-123) --------------------------------------------------------
#: Окно беспошлинной нормы на человека: норма отделяет бытовой провоз от
#: промысла, иначе пошлина бьёт первым делом по новичку с мешком репы.
TRADE_DUTY_FREE_WINDOW = Num("trade.duty_free_window")
#: По сделкам за этот срок берётся справочная цена. Сделок не было — пошлину
#: не с чего считать: сначала рынок, потом таможня.
TRADE_REFERENCE_PRICE_WINDOW = Num("trade.reference_price_window")

# --- Разведка (D-152, цена захода — D-156) ----------------------------------
#: Заход по нехоженой окрестности: минуты. Дальше растёт истощением места.
EXPLORE_ATTEMPT_MINUTES = Span("explore.attempt_minutes")
#: Потолок длительности захода, а не его длина.
EXPLORE_ATTEMPT_HOURS = Num("explore.attempt_hours")
#: Во столько раз длиннее каждый следующий заход от того же узла.
EXPLORE_EFFORT_GROWTH = Num("explore.effort_growth")
#: Цена захода полной длины; короткий стоит по времени в поле.
EXPLORE_ATTEMPT_STAMINA = Num("explore.attempt_stamina")
#: Шанс по нехоженому месту; падает на `find_decay` с каждой находкой отсюда,
#: но не ниже `find_floor`: исхоженное беднеет, а не запирается.
EXPLORE_FIND_CHANCE = Num("explore.find_chance")
EXPLORE_FIND_DECAY = Num("explore.find_decay")
EXPLORE_FIND_FLOOR = Num("explore.find_floor")
EXPLORE_VEIN_SHARE = Num("explore.vein_share")
EXPLORE_NODE_AREA = Span("explore.node_area")
EXPLORE_DISTANCE = Span("explore.distance")
EXPLORE_VEIN_RICHNESS = Span("explore.vein_richness")
EXPLORE_VEIN_STOCK = Span("explore.vein_stock")

# --- Свойства места (D-126) -------------------------------------------------
SITE_TEMP_RANGE = Span("site.temp_range")
SITE_RAIN_RANGE = Span("site.rain_range")
SITE_RIVER_SHARE = Num("site.river_share")
SITE_QUALITY_BUDGET = Num("site.quality_budget")


def declared() -> tuple[Spec, ...]:
    """Всё объявленное в этом модуле — источник для проверки на старте."""
    return tuple(
        value
        for name, value in sorted(globals().items())
        if not name.startswith("_") and isinstance(value, Spec)
    )
