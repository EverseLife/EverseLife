# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The city's own keys: its market, its money, its law, its bank, its land and houses.

A section of `registry`, cut off by roadmap stage when the registry passed the
eight hundred lines the quality bar allows (2026-09-13): the market (E1), the
coin and its printing (E2, D-270), the layout of a city, its polls and its
court (E3), the bank, credit, debt and the works fund (E4), buildings and
their meter (E3), the trade report and customs (E3).
Star-imported into the registry, which is the only module that imports it.
"""

from __future__ import annotations

from src.constants.spec import Book, Num, Span, Table

# --- Market (D-047, D-100, D-127) -------------------------------------------
MARKET_ORDER_LIFETIME = Num("market.order_lifetime")
MARKET_DEFAULT_FEE = Num("market.default_fee")
MARKET_RESERVATION_DEPOSIT = Num("market.reservation_deposit")
MARKET_RESERVATION_PERIOD = Num("market.reservation_period")
MARKET_ORPHAN_DECAY_MULTIPLIER = Num("market.orphan_decay_multiplier")
#: The terminal's own tank (D-255): how many kilograms of liquid the whole
#: counter holds across every cell. The knob that bounds the liquid
#: market's liquidity -- a bigger terminal is a bigger market.
MARKET_TANK_CAPACITY = Num("market.tank_capacity")

# --- Coin (D-016, D-086) ----------------------------------------------------
#: Coin fineness, per mille. There is no debasement mechanic any more: a coin
#: is always of this fineness, and the composition is set by recipe amounts
#: (0.9 refined + 0.1 iron).
COIN_DEFAULT_FINENESS = Num("coin.default_fineness")

# --- Emission by signatures (D-270) --------------------------------------------
#: How long a proposal to print collects signatures before it expires.
EMISSION_PROPOSAL_HOURS = Num("emission.proposal_hours")
#: The share of the right's holders whose signatures print, percent.
EMISSION_SIGNATURE_SHARE = Num("emission.signature_share")

# --- City layout (D-089, D-125) ---------------------------------------------
LAND_AREA_RING1 = Span("land.area_ring1")
#: A founded city's first ring of plots (D-089): the count the authority
#: hands out, laid at the founding since nothing is found in a city (D-319).
CITY_RING_SLOTS_BASE = Num("city.ring_slots_base")
#: By this much land gets cheaper with each **node** from the bioprinter --
#: the city centre. Nodes and not rings: a ring is a property written at
#: generation, nodes are how the city is actually walked (D-220). One number
#: answers both questions about the price of a place: what a plot costs to
#: buy and what it costs to hold, because they are the same statement.
LAND_DECAY_PER_NODE = Num("land.decay_per_node")
#: Where a city ends (D-356): the numbers of the field its outline is traced
#: in. The map drew with them long before they decided anything; now the
#: outline is whose land a node is, so the engine (`src.outline`) and the map
#: (`map/territory.ts`, off `/public/constants`) read the same six.
CITY_OUTLINE_POWER = Num("city.outline_power")
CITY_OUTLINE_REACH_SHARE = Num("city.outline_reach_share")
CITY_OUTLINE_LONE_REACH_M = Num("city.outline_lone_reach_m")
CITY_OUTLINE_CELLS_PER_STEP = Num("city.outline_cells_per_step")
CITY_OUTLINE_MAX_CELLS = Num("city.outline_max_cells")
CITY_OUTLINE_BRIDGE_CELLS = Num("city.outline_bridge_cells")

# --- Polls (D-161) -----------------------------------------------------------
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
BANK_UNSECURED_LIMIT = Num("bank.unsecured_limit")
#: Ceiling of the city bank's margin above the key rate (D-175).
BANK_CITY_MARGIN_CAP = Num("bank.city_margin_cap")
#: What the capital withholds of every income into a debtor city's treasury (D-285).
BANK_INCOME_WITHHELD_SHARE = Num("bank.income_withheld_share")
#: A city measured as a borrower (D-285): the same formula as a person's,
#: with numbers of its own -- a bigger base, because a city lends to others
#: rather than to itself, and a harsher penalty, because an overdue is the
#: city's own doing where a complaint about a person is somebody else's word.
CREDIT_CITY_BASE = Num("credit.city_base")
CREDIT_CITY_TURNOVER_SHARE = Num("credit.city_turnover_share")
CREDIT_CITY_INTEREST_SHARE = Num("credit.city_interest_share")
CREDIT_CITY_OVERDUE_PENALTY = Num("credit.city_overdue_penalty")
CREDIT_CITY_TRUST_FLOOR = Num("credit.city_trust_floor")
#: Credit limit from labour (D-173): the share of turnover, the multiple of the
#: interest paid (D-280 -- of repaid principal before it, and that was free to
#: run up), the window, the bonus for a record without overdue, the report price
#: and the trust floor.
CREDIT_TURNOVER_SHARE = Num("credit.turnover_share")
CREDIT_INTEREST_SHARE = Num("credit.interest_share")
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
#: The works fund (D-248): interest income returns to the world as pay for
#: verified labour. The recycle share is a public function of inflation; the
#: print tap starts closed (cap 0) and opens by constant, not by release.
WORKS_RECYCLE_RAMP = Num("works.recycle_ramp")
WORKS_PRINT_CAP = Num("works.print_cap")
WORKS_HOUR_RATE = Num("works.hour_rate")
WORKS_ROAD_THRESHOLD = Num("works.road_threshold")
WORKS_CITY_COFINANCE = Num("works.city_cofinance")
WORKS_OBJECT_COOLDOWN = Num("works.object_cooldown")
WORKS_PLAYER_DAILY_CAP = Num("works.player_daily_cap")
WORKS_ORDER_DEADLINE = Num("works.order_deadline")
WORKS_HAUL_KG_PER_HOUR = Num("works.haul_kg_per_hour")
#: Inflation at which the collateral lever has done its full stroke (D-170).
BANK_INFLATION_ALARM = Num("bank.inflation_alarm")
#: Handing the rate to the Council of cities (D-172): threshold, corridor and emergency return.
BANK_COUNCIL_HANDOVER_CITIES = Num("bank.council_handover_cities")
BANK_COUNCIL_RATE_DEVIATION = Num("bank.council_rate_deviation")
BANK_COUNCIL_LOCKOUT = Num("bank.council_lockout")

# --- Node meter and maintenance (D-135, D-149) ------------------------------
ENERGY_HOME_DRAW_PER_M2 = Num("energy.home_draw_per_m2")

# --- Buildings and construction (D-106, D-125, D-131, D-218) ----------------
#: How much building area one work place takes: a machine or furniture.
BUILD_SLOTS_PER_AREA = Num("build.slots_per_area")
#: How much cargo fits on a square metre of floor (D-192). What lies in a chest
#: takes no floor: that is the whole point of a chest.
BUILD_FLOOR_PER_M2 = Num("build.floor_per_m2")
#: The smallest footprint that is still a building and not a lean-to. There is
#: no matching maximum: the plot is the ceiling, and it is a different plot
#: every time (D-218).
BUILD_AREA_MIN = Num("build.area_min")
#: Stamina the owner pays to start the build, per m2 of usable area (D-266).
BUILD_START_STAMINA_PER_M2 = Num("build.start_stamina_per_m2")
#: Assembly labour: hours per square metre. Construction is work, not a button.
BUILD_LABOR_PER_M2 = Num("build.labor_per_m2")
#: The building type settles three things at once (D-218): what goes into the
#: wall per square metre of floor, how much dearer each next floor is, and how
#: fast the house decays. Height has no ceiling at all -- a twenty-storey log
#: house may be built, and the bill refuses more convincingly than a rule.
BUILD_TYPES = Book("build.types")
BUILD_FLOOR_GROWTH = Table("build.floor_growth_by_type")
BUILD_DECAY = Table("build.decay_by_type")
#: Repair (D-145, D-218): what a house is built of is what it is mended with,
#: this share of the bill for lifting condition from nothing to full, and this
#: share of the raising labour. The walls stand -- hence cheaper than building.
BUILD_REPAIR_MATERIALS_K = Num("build.repair_materials_k")
BUILD_REPAIR_LABOR_K = Num("build.repair_labor_k")
#: Demolishing a house (D-205): the work is a share of the building's labour, and
#: a share of the bill of materials comes back. Neither is a whole: taking a
#: house apart is quicker than raising it and never free of breakage.
BUILD_DEMOLISH_LABOR_K = Num("build.demolish_labor_k")
BUILD_DEMOLISH_SALVAGE = Num("build.demolish_salvage")
#: A flight of stairs, in seconds (D-247): every floor above the ground is a
#: node of its own, and one climbs to it. Short, because a house is a room one
#: walks through and not ground one crosses -- but not free: a workshop on the
#: eighth floor must be a decision, not a free upgrade of the ground one.
BUILD_STAIR_SECONDS = Num("build.stair_seconds")

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
