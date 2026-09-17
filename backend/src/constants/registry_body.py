# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The body's own keys: its stamina, its way, its load, its food, its voice, its death.

A section of `registry`, cut off by roadmap stage when the registry passed the
eight hundred lines the quality bar allows (2026-09-13): what a person does
with a body before any city or machine -- walks, hauls and lays roads (E2),
carries and eats (E1-E2), is wounded, speaks to the room and over the Net,
dies and is printed again (E3), and the fee a device pays to bring a session
in (D-110).
Star-imported into the registry, which is the only module that imports it.
"""

from __future__ import annotations

from src.constants.spec import Book, Flag, Num, Span, Table

# --- Body (20-systems/00-character, D-091) ----------------------------------
BODY_STAMINA_MAX = Num("body.stamina_max")
BODY_DRAIN_RATE = Span("body.drain_rate")
BODY_FOOD_RESTORE = Num("body.food_restore")
BODY_DIET_VARIETY_BONUS = Num("body.diet_variety_bonus")
BODY_HIBERNATION_RATE = Num("body.hibernation_rate")
BODY_HIBERNATION_HOME_K = Num("body.hibernation_home_k")

# --- Travel (D-045, D-107, D-147, D-319) -------------------------------------
#: Time is distance (D-319): an edge between two surface nodes takes the
#: metres between them at this pace, times the surface's multiplier (D-107).
#: A city step is that too -- `travel.city_step` was seconds by decree and is
#: the ring's metres at this pace now.
TRAVEL_WALK_SPEED_KMH = Num("travel.walk_speed_kmh")
#: How many times longer the off-road is under full snow (D-338): the wild
#: and the trail, and the scout's walk, by the season's snow on the way.
TRAVEL_SNOW_MULTIPLIER = Num("travel.snow_multiplier")
TRAVEL_STAMINA_PER_HOUR = Num("travel.stamina_per_hour")
TRANSPORT_STAMINA_K = Num("transport.stamina_k")

# --- Transport (D-107, D-129, D-157) ----------------------------------------
#: Hold capacity and speed -- one layout by one key: two would diverge. The
#: key is the vault's word ("barrow", "wagon"), not the item name.
TRANSPORT_CAPACITY = Table("transport.capacity")
TRANSPORT_SPEED_K = Table("transport.speed_k")
#: From this capacity a vehicle is heavy and needs a paved highway.
TRANSPORT_HEAVY_FROM = Num("transport.heavy_from")

# --- Roads (D-107, D-158, D-252, D-319) -------------------------------------
#: The surface ladder an edge is walked at; the trail itself is worn in by
#: feet and fades by `path.*` (`registry_map`).
ROAD_TRAIL_MULTIPLIER = Num("road.trail_multiplier")
ROAD_ROAD_MULTIPLIER = Num("road.road_multiplier")
ROAD_PAVED_MULTIPLIER = Num("road.paved_multiplier")
#: Road as work on an edge (D-107, D-158): surface per surface tier, hours
#: to lay, and how much condition an untravelled road loses per day.
ROAD_SURFACE_PER_EDGE = Num("road.surface_per_edge")
ROAD_BUILD_HOURS = Num("road.build_hours")
ROAD_DECAY_RATE = Num("road.decay_rate")
#: Multiplier to the decay rate by what the edge was laid from (D-252):
#: asphalt sags at half the pace of gravel, and that is its whole point.
ROAD_DECAY_BY_PAVING = Table("road.decay_by_paving")
#: Below the ladder (D-319): an edge laid with the world and never walked.
#: Slower than a trodden trail, and no vehicle passes either.
ROAD_WILD_MULTIPLIER = Num("road.wild_multiplier")

# --- Inventory (20-systems/04-items, D-146) ---------------------------------
INVENTORY_CARRY_MASS = Num("inventory.carry_mass")
INVENTORY_CARRY_VOLUME = Num("inventory.carry_volume")
#: How many kilograms worn gear adds: backpack and exoskeleton raise the limit,
#: clothes and armour take the slot but add nothing to carry.
#: The carry model (D-268): an exoskeleton raises the limit -- while a charged
#: battery rides along -- and a pack lightens the first kilograms it holds.
INVENTORY_EXO_BONUS = Table("inventory.exo_bonus")
INVENTORY_PACK = Book("inventory.pack")
GEAR_EXO_ENERGY_PER_HOUR = Num("gear.exo_energy_per_hour")
INVENTORY_MASS_BY_KIND = Table("inventory.mass_by_kind")

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
#: Crowding instead of furnishing (D-349): what stands in a room no longer
#: changes what is overheard in it. What does is how much floor there is
#: per head -- this much space is the quiet one person needs -- and the
#: floor and ceiling keep the pair in its banks: heads are counted twice,
#: once in the sum and once in the multiplier.
CHAT_LEAK_SPACE_PER_PERSON = Num("chat.leak_space_per_person")
CHAT_LEAK_CROWDING_MIN = Num("chat.leak_crowding_min")
CHAT_LEAK_CROWDING_MAX = Num("chat.leak_crowding_max")
#: The Net's delay: seconds of delay per second of the road between the two
#: correspondents (D-222). Nought would be the instant link of D-010.
COMM_DELAY_PER_SECOND = Num("comm.delay_per_second")

# --- Death and body printing (D-012, D-028, D-032, D-033, D-040) ------------
DEATH_FIRST_BODY_INSTANT = Flag("death.first_body_instant")
DEATH_SALVAGE_RATIO = Num("death.salvage_ratio")
DEATH_IRON_COST = Num("death.iron_cost")
DEATH_PRINT_TIME_CITY = Num("death.print_time_city")
DEATH_PRINT_TIME_CAPITAL = Num("death.print_time_capital")
ENERGY_BODY_PRINT = Num("energy.body_print")
