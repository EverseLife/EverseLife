# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The life of a sown bed: three hidden scales and how time moves them (D-293).

Moisture, health and growth are the bed's state at one moment (`Life`), and
everything between two moments is a pure function of the elapsed time, the
place's weather and the culture's norms. Nothing here touches the database:
`season.settle` writes what this computes, the survey computes it and writes
nothing, and the world tick writes it for every bed -- one function, three
callers, so a read and a write never disagree about the same strip.

Moisture leaves as a share of what is there (`farm.dry_rate` per Terran day,
D-008): wet ground dries fast, dry ground barely, and nothing ever reaches
nought. The culture drinks at its own pace (`farm.water_by_need`), heat
quickens the loss (`farm.dry_per_degree` past `farm.dry_temp_ref`), rain
covers a share of it (`site.rain_water_offset`) and a river halves it
(`farm.river_dry_share`). Health falls in proportion to how far the moisture
sits outside the culture's band (`farm.stress_per_point` per point per day),
softened by the cultivar's hardiness (D-261), and heals inside it; growth
adds its nominal share of the cycle scaled by health and by a feeding's
boost, and a boost lasts to the end of the stage it was given in.

Weeds come up with the crop (D-295): `farm.weed_per_day` scaled by the
land's fertility -- rich soil feeds them too -- and a full cover drags the
growth by `farm.weed_drag` and quickens the drying by `farm.weed_thirst`;
a weeding clears them. Whether the stand was thinned is a fact of the
sowing, not of time: the harvest reads it.

Stepped by the hour rather than integrated in closed form: temperature
breathes with the planetary day, and a boost ends at a stage bound the
integral would have to know in advance. An hour is far below anything a
player can see, and the same steps from the same stamp give the same numbers
wherever they are taken.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src.constants import Constants
from src.constants import registry as R
from src.constants.catalog import Plant
from src.runtime import FARM_STEP_HOURS
from src.units import HARDINESS_SCALE, PERCENT, SCALE_MAX, SCALE_MIN

#: The stages of growth, in order. The first begins at nought and the last at
#: `SCALE_MAX`; the bounds between them are the vault's (`farm.stage_bounds`).
#: Common to every crop (D-057): a symptom is what is seen, and so is a stage.
SPROUT = "sprout"
RIPE = "ripe"
STAGES: tuple[str, ...] = (SPROUT, "leaf", "bloom", "fill", RIPE)

#: The words the health is said in, from the strongest down (`farm.health_bands`).
#: The number itself is never shown: the player reads a word (D-293).
HEALTH_WORDS: tuple[str, ...] = ("strong", "weak", "sick", "dying")

#: What the bed shows to everybody: a sign, never a number (D-057).
THIRST = "thirst"
SOAKED = "soaked"
PALE = "pale"
BURN = "burn"
FAT = "fat"
#: Wave 2 (D-295): weeds past the threshold, and an unthinned stand once
#: it is past sprouting -- shown to everybody, priced by the culture.
WEEDY = "weedy"
CROWDED = "crowded"

#: What a feeding did, as the bed remembers it for the stage (`Plot.fed`).
BOOST = "boost"
OVERFED = "overfed"


@dataclass(frozen=True)
class Norms:
    """What the culture asks of its bed -- the crop's numbers, or the cultivar's."""

    band_min: float
    band_max: float
    #: The pace the culture drinks at: a multiplier to the drying rate.
    drink: float
    hardiness: float
    cycle_days: float


@dataclass(frozen=True)
class Weather:
    """The place as the bed feels it: the rainfall on the vault's scale,
    whether a river feeds the ground, and the temperature at an hour offset
    -- a function, because it breathes with the planetary day (D-261)."""

    rain: float
    river: bool
    temperature_at: Callable[[float], float | None]


@dataclass(frozen=True)
class Life:
    """The bed's three scales at one moment, and the boost a feeding gave."""

    moisture: float
    health: float
    growth: float
    #: Per cent of speed a feeding added, until the end of the stage it was
    #: given in (D-293): the stage is remembered so that the boost is dropped
    #: exactly once the growth crosses that stage's bound.
    boost: float = 0.0
    boost_stage: str | None = None
    #: Weeds on the bed, 0-100, and whether this sowing was thinned (D-295).
    weeds: float = 0.0
    thinned: bool = False

    @property
    def dead(self) -> bool:
        return self.health <= SCALE_MIN

    @property
    def ripe(self) -> bool:
        return self.growth >= SCALE_MAX


def norms(constants: Constants, plant: Plant, signs: Mapping[str, Any]) -> Norms:
    """The culture's norms, the cultivar's traits over the crop's (D-057).

    The band is read by the crop's thirst (`requires.water`) through the
    vault's table: the requirement is data, the scale is derived (D-136).
    Indexed, never defaulted: a thirst the tables do not know is a hole in
    the data, and a crop quietly drinking as a middling one would hide it.
    """
    need = str(int(signs.get("water", plant.requires.water)))
    band = constants[R.FARM_MOISTURE_BY_NEED][need]
    return Norms(
        band_min=float(band["min"]),
        band_max=float(band["max"]),
        drink=float(constants[R.FARM_WATER_BY_NEED][need]),
        hardiness=float(signs.get("hardiness", plant.traits.hardiness)),
        cycle_days=float(signs.get("cycle_days", plant.cycle_days)),
    )


def dry_rate(
    constants: Constants, norms: Norms, weather: Weather, temperature: float | None
) -> float:
    """The share of the moisture that leaves per Terran day, here and now.

    A node without a temperature record -- old ones, a hull's hydroponics --
    dries at the reference pace: absence of a record is not a climate.
    """
    rate = constants[R.FARM_DRY_RATE] / PERCENT * norms.drink
    if temperature is not None:
        warmth = temperature - constants[R.FARM_DRY_TEMP_REF]
        rate *= max(0.0, 1 + constants[R.FARM_DRY_PER_DEGREE] / PERCENT * warmth)
    rain = min(max(weather.rain, 0.0), PERCENT) / PERCENT
    rate *= max(0.0, 1 - constants[R.SITE_RAIN_WATER_OFFSET] / PERCENT * rain)
    if weather.river:
        rate *= constants[R.FARM_RIVER_DRY_SHARE] / PERCENT
    return rate


def weeds_thirst(constants: Constants, weeds: float) -> float:
    """The drying multiplier the weeds add: they drink beside the crop (D-295)."""
    return 1 + weeds / SCALE_MAX * constants[R.FARM_WEED_THIRST] / PERCENT


def thinning_open(constants: Constants, stage: str) -> bool:
    """Whether a stand in this stage can still be thinned: up to `farm.thin_until`."""
    return STAGES.index(stage) <= STAGES.index(str(constants[R.FARM_THIN_UNTIL]))


def stage_of(constants: Constants, growth: float) -> str:
    """Which stage the growth is in: the bounds are the vault's."""
    if growth >= SCALE_MAX:
        return RIPE
    bounds = constants[R.FARM_STAGE_BOUNDS]
    current = SPROUT
    for stage in STAGES[1:-1]:
        if growth >= bounds[stage]:
            current = stage
    return current


def health_word(constants: Constants, health: float) -> str:
    """The word for the health: the first band the number reaches, from the top."""
    bands = constants[R.FARM_HEALTH_BANDS]
    for word in HEALTH_WORDS[:-1]:
        if health >= bands[word]:
            return word
    return HEALTH_WORDS[-1]


def advance(
    constants: Constants,
    norms: Norms,
    weather: Weather,
    life: Life,
    *,
    hours: float,
    day_hours: float,
    fertility: float = 0.0,
) -> Life:
    """Move the bed `hours` on from the moment `life` was true.

    `weather.temperature_at` counts its hours from that same moment. A dead
    bed stays dead and a step that ends the health ends the walk at once:
    what grew in a bed's last hour is nobody's harvest. `fertility` feeds the
    weeds (D-295); bare rock -- the default -- grows none.
    """
    if hours <= 0 or life.dead:
        return life
    relief = 1 - constants[R.FARM_HARDINESS_RELIEF] / PERCENT * norms.hardiness / HARDINESS_SCALE
    stress = constants[R.FARM_STRESS_PER_POINT]
    heal = constants[R.FARM_HEAL_PER_DAY]
    sprout = constants[R.FARM_WEED_PER_DAY] * max(fertility, 0.0) / SCALE_MAX
    drag = constants[R.FARM_WEED_DRAG] / PERCENT
    #: The nominal pace: a healthy, unfed bed ripens in the catalog's cycle.
    pace = SCALE_MAX / max(norms.cycle_days, 1.0 / day_hours)

    moisture, health, growth = life.moisture, life.health, life.growth
    boost, boost_stage, weeds = life.boost, life.boost_stage, life.weeds
    passed = 0.0
    while passed < hours:
        step = min(FARM_STEP_HOURS, hours - passed)
        days = step / day_hours
        weeds = min(SCALE_MAX, weeds + sprout * days)
        rate = dry_rate(constants, norms, weather, weather.temperature_at(passed))
        moisture *= math.exp(-rate * weeds_thirst(constants, weeds) * days)

        gap = max(0.0, norms.band_min - moisture, moisture - norms.band_max)
        if gap > 0:
            health -= stress * gap * relief * days
        else:
            health = min(SCALE_MAX, health + heal * days)
        if health <= SCALE_MIN:
            return Life(moisture, SCALE_MIN, growth, weeds=weeds, thinned=life.thinned)

        if growth < SCALE_MAX:
            held = 1 - weeds / SCALE_MAX * drag
            growth = min(
                SCALE_MAX,
                growth + pace * (health / SCALE_MAX) * (1 + boost / PERCENT) * held * days,
            )
            if boost_stage is not None and stage_of(constants, growth) != boost_stage:
                boost, boost_stage = 0.0, None
        passed += step
    return Life(moisture, health, growth, boost, boost_stage, weeds, life.thinned)


def symptoms(
    constants: Constants,
    norms: Norms,
    life: Life,
    *,
    stage: str,
    fertility: float,
    fertility_needed: float,
    fed: Iterable[Mapping[str, Any]],
) -> list[str]:
    """What the bed shows, to everybody alike (D-057): signs, never norms.

    `fed` is what this stage was given: a wrong feeding shows as a burn and a
    repeated one as a bed running to leaf, until the stage is over. Weeds show
    past `farm.weed_seen`; an unthinned stand shows as crowded from the leaf
    stage on -- to every crop, though only some pay for it (D-295).
    """
    seen: list[str] = []
    if life.moisture < norms.band_min:
        seen.append(THIRST)
    elif life.moisture > norms.band_max:
        seen.append(SOAKED)
    if fertility < fertility_needed:
        seen.append(PALE)
    effects = {str(row.get("effect")) for row in fed}
    if BURN in effects:
        seen.append(BURN)
    if OVERFED in effects:
        seen.append(FAT)
    if life.weeds >= constants[R.FARM_WEED_SEEN]:
        seen.append(WEEDY)
    if not life.thinned and stage not in (SPROUT, RIPE):
        seen.append(CROWDED)
    return seen
