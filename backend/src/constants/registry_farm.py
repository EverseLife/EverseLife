# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The farm's own keys: the bed, its care, its pests and its seed (D-118, D-296, D-299, D-057).

A section of `registry`, cut off by roadmap stage when the registry passed the
eight hundred lines the quality bar allows (2026-09-13): land farming in plots
(E2) and the breeding of what grows on them.
Star-imported into the registry, which is the only module that imports it.
"""

from __future__ import annotations

from src.constants.spec import Bands, FormulaRef, Num, Span, Table, Text, Words

# --- Farming (D-118, D-105) -------------------------------------------------
FARM_PLOT_MIN_AREA = Num("farm.plot_min_area")
FARM_PLOW_TIME_PER_M2 = Num("farm.plow_time_per_m2")
FARM_SEED_RATE = Num("farm.seed_rate")
FARM_CARE_TIME_PER_M2 = Num("farm.care_time_per_m2")
FARM_PLOT_OVERHEAD = Num("farm.plot_overhead")
#: Litres per square metre from dry ground to full moisture (D-296): a
#: watering to a target takes its share of this.
FARM_WATER_PER_M2 = Num("farm.water_per_m2")
#: The culture's thirst (requires.water 1-3) as the pace it drinks at: a
#: multiplier to the drying rate (D-296; the watering norm before it).
FARM_WATER_BY_NEED = Table("farm.water_by_need")
#: The band of moisture a culture asks for, by its thirst (D-296).
FARM_MOISTURE_BY_NEED = Bands("farm.moisture_by_need")
#: What the ground holds when the seed goes in.
FARM_SOWN_MOISTURE = Num("farm.sown_moisture")
#: Moisture leaves as a share of what is there, per Terran day -- at the
#: reference temperature, faster or slower by the degree (D-296).
FARM_DRY_RATE = Num("farm.dry_rate")
FARM_DRY_TEMP_REF = Num("farm.dry_temp_ref")
FARM_DRY_PER_DEGREE = Num("farm.dry_per_degree")
#: A river slows the drying to this share: it makes watering rare, not free of labour.
FARM_RIVER_DRY_SHARE = Num("farm.river_dry_share")
#: Health lost per day for every point of moisture outside the band, and
#: regained per day inside it (D-296).
FARM_STRESS_PER_POINT = Num("farm.stress_per_point")
FARM_HEAL_PER_DAY = Num("farm.heal_per_day")
#: How much hardiness 5/5 softens the stress (D-261, D-296).
FARM_HARDINESS_RELIEF = Num("farm.hardiness_relief")
#: Health lost per day for every degree the moment's temperature stands
#: outside the culture's `requires.temp`; below the band the bed also
#: stops growing (D-338).
FARM_TEMP_STRESS_PER_DEGREE = Num("farm.temp_stress_per_degree")
#: Points of moisture an hour of downpour gives a bed, a lighter rain its
#: share, and never past the top of the culture's band (D-338).
FARM_RAIN_PER_HOUR = Num("farm.rain_per_hour")
#: The stages' lower bounds on the growth scale, and the words of health by
#: their lower bounds: the player reads a stage and a word, never a number.
FARM_STAGE_BOUNDS = Table("farm.stage_bounds")
FARM_HEALTH_BANDS = Table("farm.health_bands")
#: A feeding not in the culture's table burns this much health; a repeated
#: one in a stage costs this share of the harvest (D-296).
FARM_FEED_WRONG_BURN = Num("farm.feed_wrong_burn")
FARM_OVERFEED_YIELD_PENALTY = Num("farm.overfeed_yield_penalty")
#: Weeds (D-297): up with the crop, faster on rich land; they drink beside
#: it and drag its growth, and a weeding clears them. Seen from a threshold.
FARM_WEED_PER_DAY = Num("farm.weed_per_day")
FARM_WEED_DRAG = Num("farm.weed_drag")
FARM_WEED_THIRST = Num("farm.weed_thirst")
FARM_WEED_SEEN = Num("farm.weed_seen")
#: Crowding (D-297): an unthinned stand loses by the culture's density_risk;
#: thinning costs its own share and works up to a stage.
FARM_CROWD_PENALTY = Num("farm.crowd_penalty")
FARM_THIN_LOSS = Num("farm.thin_loss")
FARM_THIN_UNTIL = Text("farm.thin_until")
#: The pests of a bed (D-299): a pressure that builds from a mistake of
#: care, the trouble it discharges into, and what puts it out.
FARM_PEST_PRESSURE = Num("farm.pest_pressure")
FARM_PEST_RELIEF = Num("farm.pest_relief")
FARM_CROWD_PEST = Num("farm.crowd_pest")
FARM_PEST_ONSET = Num("farm.pest_onset")
FARM_PEST_STRESS = Num("farm.pest_stress")
FARM_PEST_SEEN = Num("farm.pest_seen")
FARM_DISEASE_SPREAD = Num("farm.disease_spread")
FARM_PROTECTANT_PER_M2 = Num("farm.protectant_per_m2")
FARM_PROTECT_DAYS = Table("farm.protect_days")
FARM_PEST_CURE = Words("farm.pest_cure")
#: From this built share of the node's ground the place loses a light step (D-261).
FARM_SHADE_BUILT_SHARE = Num("farm.shade_built_share")
FARM_SOIL_DEPLETION = Num("farm.soil_depletion")
#: Extra depletion for a repeat of the same crop in a row (D-256).
FARM_MONOCULTURE_PENALTY = Num("farm.monoculture_penalty")
#: Ceiling of the fertility/required ratio: rich land is an edge, not a multiplier (D-256).
FARM_SOIL_SHARE_CAP = Num("farm.soil_share_cap")
FARM_FALLOW_RECOVERY = Num("farm.fallow_recovery")
#: Fertilizers (D-264, D-291): one dose for the class, a strength per thing.
#: The vault's promise "mineral gives most of all" is two rows side by side,
#: and a third fertilizer is a third row rather than a third constant.
FARM_FERTILIZER_PER_M2 = Num("farm.fertilizer_per_m2")
FARM_FERTILIZER_RECOVERY = Table("farm.fertilizer_recovery")

# --- Breeding (D-057, D-067) ------------------------------------------------
#: Seed multiplication ratio at full care on healthy soil (D-257).
FARM_SEED_RETURN = Num("farm.seed_return")
BREED_INHERIT_DRIFT = FormulaRef("breed.inherit_drift")
BREED_NOVEL_TRAIT_CHANCE = Num("breed.novel_trait_chance")
BREED_HYBRID_DECAY = Num("breed.hybrid_decay")
BREED_GENERATIONS_TO_STABILIZE = Span("breed.generations_to_stabilize")
BREED_DEGRADATION_PER_GEN = Num("breed.degradation_per_gen")
BREED_DISTINCTNESS_THRESHOLD = Num("breed.distinctness_threshold")
#: The wild ancestor's traits as multipliers over the base cultivar (D-260).
BREED_WILD_TRAITS = Table("breed.wild_traits")
#: A novel trait's shift from the middle: clears the distinctness threshold
#: with margin and is no longer tied to the inheritance drift (D-260).
BREED_NOVEL_TRAIT_SHIFT = Num("breed.novel_trait_shift")
#: F1 heterosis: the nursery lot's vigor above one hundred, one sowing long (D-260).
BREED_HYBRID_VIGOR = Num("breed.hybrid_vigor")
