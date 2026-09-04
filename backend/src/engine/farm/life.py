# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The life of a sown bed: three hidden scales and how time moves them (D-296).

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

Weeds come up with the crop (D-297): `farm.weed_per_day` scaled by the
land's fertility -- rich soil feeds them too -- and a full cover drags the
growth by `farm.weed_drag` and quickens the drying by `farm.weed_thirst`;
a weeding clears them. Whether the stand was thinned is a fact of the
sowing, not of time: the harvest reads it.

Pests come for a mistake and for nothing else (D-299). Four of them, and
each has its own: soaking breeds a fungus, drought a mite, weeds the
insects, a wrong or repeated feeding the bacteria. A hidden pressure builds
by `farm.pest_pressure` a day times the share of that very mistake, the
cultivar's `disease_risk` and the crowd of an unthinned stand
(`farm.crowd_pest`); with the mistake gone it falls by `farm.pest_relief`.
No roll anywhere: the same care gives the same outcome, and a bed kept in
its band, weeded and thinned never falls ill at all. Past the scale the
trouble strikes -- `farm.pest_onset` of the bed -- and spreads by
`farm.disease_spread` a day within the plot, cutting the harvest by its
share and taking `farm.pest_stress` of health at full cover. One trouble at
a time: while a bed is struck the other three pressures stand still.

Stepped by the hour rather than integrated in closed form: temperature
breathes with the planetary day, and a boost ends at a stage bound the
integral would have to know in advance. An hour is far below anything a
player can see, and the same steps from the same stamp give the same numbers
wherever they are taken.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from src.constants import ConstantError, Constants
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
#: The number itself is never shown: the player reads a word (D-296).
HEALTH_WORDS: tuple[str, ...] = ("strong", "weak", "sick", "dying")

#: What the bed shows to everybody: a sign, never a number (D-057).
THIRST = "thirst"
SOAKED = "soaked"
PALE = "pale"
BURN = "burn"
FAT = "fat"
#: Wave 2 (D-297): weeds past the threshold, and an unthinned stand once
#: it is past sprouting -- shown to everybody, priced by the culture.
WEEDY = "weedy"
CROWDED = "crowded"
#: Wave 3 (D-299): what a struck bed shows past `farm.pest_seen`. The sign
#: says what the eye sees and never names the trouble or its cure: that
#: coupling is the agrotech text's to teach (D-057).
SPOTS = "spots"
WEB = "web"
BITTEN = "bitten"
ROT = "rot"

#: The four pests, and the sign each of them shows.
FUNGUS = "fungus"
MITE = "mite"
INSECT = "insect"
BACTERIA = "bacteria"
PESTS: tuple[str, ...] = (FUNGUS, MITE, INSECT, BACTERIA)
PEST_SIGNS: Mapping[str, str] = {
    FUNGUS: SPOTS,
    MITE: WEB,
    INSECT: BITTEN,
    BACTERIA: ROT,
}

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
    #: How much the cultivar fears the pests, on the traits' five-point
    #: scale (D-261): the multiplier of every pressure (D-299).
    pest_risk: float


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
    #: given in (D-296): the stage is remembered so that the boost is dropped
    #: exactly once the growth crosses that stage's bound.
    boost: float = 0.0
    boost_stage: str | None = None
    #: Weeds on the bed, 0-100, and whether this sowing was thinned (D-297).
    weeds: float = 0.0
    thinned: bool = False
    #: What each stage of this sowing was fed and what it did: stage ->
    #: rows of `{goods, effect}`. State of the bed, so it travels with it:
    #: a wrong or repeated feeding is what the bacteria come for (D-299).
    fed: Mapping[str, Any] = field(default_factory=dict)
    #: The pests (D-299): the pressure of each, the trouble that struck
    #: and the share of the bed it has taken.
    pest: Mapping[str, float] = field(default_factory=dict)
    illness: float = 0.0
    illness_kind: str | None = None

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
        pest_risk=float(signs.get("disease_risk", plant.traits.disease_risk)),
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
    """The drying multiplier the weeds add: they drink beside the crop (D-297)."""
    return 1 + weeds / SCALE_MAX * constants[R.FARM_WEED_THIRST] / PERCENT


def thinning_open(constants: Constants, stage: str) -> bool:
    """Whether a stand in this stage can still be thinned: up to `farm.thin_until`.

    The constant is a word, and a word the stages do not know is a hole in
    the data: named as such, not a `ValueError` on the first thinning.
    """
    until = str(constants[R.FARM_THIN_UNTIL])
    if until not in STAGES:
        raise ConstantError(f"farm.thin_until: {until!r} is not a stage of {STAGES}")
    return STAGES.index(stage) <= STAGES.index(until)


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


def pest_drives(norms: Norms, life: Life, stage: str) -> dict[str, float]:
    """How wrong the bed is, per pest, as a share from nought to one (D-299).

    One mistake of care to each trouble, and nothing else feeds them: the
    moisture over the band soaks the roots and breeds the fungus, the
    moisture under it dries the leaf and breeds the mite, the weeds shelter
    the insects, and a burnt or an overfed stage opens the way to the
    bacteria. A bed inside its band, weeded and fed by the table drives
    nothing at all -- which is the whole promise of the decision.
    """
    given = list(life.fed.get(stage, ()) or ())
    spoiled = {str(row.get("effect")) for row in given} & {BURN, OVERFED}
    return {
        FUNGUS: min(
            1.0, max(0.0, life.moisture - norms.band_max) / max(1.0, SCALE_MAX - norms.band_max)
        ),
        MITE: min(1.0, max(0.0, norms.band_min - life.moisture) / max(1.0, norms.band_min)),
        INSECT: min(1.0, max(0.0, life.weeds) / SCALE_MAX),
        BACTERIA: 1.0 if spoiled else 0.0,
    }


def advance(
    constants: Constants,
    norms: Norms,
    weather: Weather,
    life: Life,
    *,
    hours: float,
    day_hours: float,
    fertility: float,
    guarded: Mapping[str, float] | None = None,
) -> Life:
    """Move the bed `hours` on from the moment `life` was true.

    `weather.temperature_at` counts its hours from that same moment. A dead
    bed stays dead and a step that ends the health ends the walk at once:
    what grew in a bed's last hour is nobody's harvest. `fertility` feeds the
    weeds (D-297) and is asked for on purpose: a caller that forgot it would
    grow a bed without a weed and nobody would say so.

    `guarded` is how many hours of a treatment are left at the start of this
    walk, per pest (D-299); a bed nobody treated is guarded against nothing,
    and that is the default. A guard freezes its own pressure and holds the
    trouble it cures where it stands -- it never takes back what was struck.
    """
    if hours <= 0 or life.dead:
        return life
    softer = 1 - constants[R.FARM_HARDINESS_RELIEF] / PERCENT * norms.hardiness / HARDINESS_SCALE
    stress = constants[R.FARM_STRESS_PER_POINT]
    heal = constants[R.FARM_HEAL_PER_DAY]
    sprout = constants[R.FARM_WEED_PER_DAY] * max(fertility, 0.0) / SCALE_MAX
    drag = constants[R.FARM_WEED_DRAG] / PERCENT
    #: The nominal pace: a healthy, unfed bed ripens in the catalog's cycle.
    pace = SCALE_MAX / max(norms.cycle_days, 1.0 / day_hours)

    #: The pests' own numbers (D-299), read once: the walk is by the hour.
    guard = dict(guarded or {})
    pest = {name: float(life.pest.get(name, 0.0)) for name in PESTS}
    struck, illness = life.illness_kind, life.illness
    building = constants[R.FARM_PEST_PRESSURE]
    relief = constants[R.FARM_PEST_RELIEF]
    crowd = 1.0 if life.thinned else 1 + constants[R.FARM_CROWD_PEST] / PERCENT
    fear = crowd * max(0.0, norms.pest_risk) / HARDINESS_SCALE
    spread = constants[R.FARM_DISEASE_SPREAD]
    ache = constants[R.FARM_PEST_STRESS]

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
            health -= stress * gap * softer * days
        else:
            health = min(SCALE_MAX, health + heal * days)

        #: The pests (D-299). One trouble at a time: while a bed is struck the
        #: other three pressures stand still -- the farmer is fighting what came.
        here = Life(moisture, health, growth, weeds=weeds, thinned=life.thinned, fed=life.fed)
        if struck is None:
            drives = pest_drives(norms, here, stage_of(constants, growth))
            for name in PESTS:
                if passed < guard.get(name, 0.0):
                    continue
                drive = drives[name]
                if drive > 0:
                    pest[name] += building * drive * fear * days
                else:
                    pest[name] = max(0.0, pest[name] - relief * days)
                if pest[name] >= SCALE_MAX:
                    struck, illness, pest[name] = name, constants[R.FARM_PEST_ONSET], 0.0
                    break
        else:
            if passed >= guard.get(struck, 0.0):
                illness = min(SCALE_MAX, illness + spread * days)
            health -= ache * illness / SCALE_MAX * days

        if health <= SCALE_MIN:
            return Life(
                moisture,
                SCALE_MIN,
                growth,
                weeds=weeds,
                thinned=life.thinned,
                fed=life.fed,
                pest=pest,
                illness=illness,
                illness_kind=struck,
            )

        if growth < SCALE_MAX:
            held = 1 - weeds / SCALE_MAX * drag
            growth = min(
                SCALE_MAX,
                growth + pace * (health / SCALE_MAX) * (1 + boost / PERCENT) * held * days,
            )
            if boost_stage is not None and stage_of(constants, growth) != boost_stage:
                boost, boost_stage = 0.0, None
        passed += step
    return Life(
        moisture,
        health,
        growth,
        boost,
        boost_stage,
        weeds,
        life.thinned,
        life.fed,
        pest,
        illness,
        struck,
    )


def symptoms(
    constants: Constants,
    norms: Norms,
    life: Life,
    *,
    fertility: float,
    fertility_needed: float,
    fed: Iterable[Mapping[str, Any]],
) -> list[str]:
    """What the bed shows, to everybody alike (D-057): signs, never norms.

    `fed` is what this stage was given: a wrong feeding shows as a burn and a
    repeated one as a bed running to leaf, until the stage is over. Weeds show
    past `farm.weed_seen`; an unthinned stand shows as crowded from the leaf
    stage on -- to every crop, though only some pay for it (D-297).
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
    #: From the leaf stage to the harvest itself: the crowd is paid for at
    #: the reaping, and the sign explaining the shortfall must not go out first.
    if not life.thinned and STAGES.index(stage_of(constants, life.growth)) >= STAGES.index("leaf"):
        seen.append(CROWDED)
    #: What the trouble looks like, past the threshold (D-299): the sign, and
    #: never its name -- which class puts it out is the text's to say.
    if life.illness_kind is not None and life.illness >= constants[R.FARM_PEST_SEEN]:
        seen.append(PEST_SIGNS[life.illness_kind])
    return seen
