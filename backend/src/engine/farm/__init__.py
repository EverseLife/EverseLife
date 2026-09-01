# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
"""Farming by plots (D-118, D-105, D-057).

Cultivars, seeds and crossing live next door in `engine/breed.py`: here is the
land and the cycle, there is what grows on it.

The economy's second pedal: mining is limited by the player's attention,
farming by land and time. The plot cycle: ploughing -> sowing -> care ->
growth -> harvest -> fallow or the next crop. Growth runs offline, care only
on foot: fully offline farming does not go, otherwise it is a printing press
(D-118).

## Where each formula came from

Numbers come from `farm.*` and `build/plants.json`, the order of steps is the
engine's business.

**A day.** All farming terms are given "in days", and a day here is planetary:
`time.day_terra` hours (D-008). Terra has no other day length.

**Care.** Once a day, for the whole plot. The round time is a vault formula:
`farm.plot_overhead + farm.care_time_per_m2 * area`; water is
`farm.water_per_m2 * area * thirst * (1 - rain covered)` (D-261): the
culture's thirst is `farm.water_by_need` over `requires.water`, and rain
covers up to `site.rain_water_offset` of the round. By a river it is taken
from the river, in a dry place it is carried as an item. Skipped days do not
zero the harvest but cut it by `farm.neglect_penalty` each, softened by the
cultivar's hardiness (`farm.hardiness_relief`, D-261): a holiday is not
punished, but neglect shows -- less on a forgiving crop.

**Climate gate (D-261).** Sowing checks the place: the node's daily
temperature band (mean from exploration, swing from `planet.temp_swing`)
must fit the culture's `requires.temp` whole, and the day's light
(`engine/climate.py`: 3 in the open, less under woods and buildings) must
reach `requires.light`. A node without a temperature record -- old ones, a
ship's hydroponics bay -- carries no gate.

**Harvest.** "Proportional to area, fertility and care quality":

    yield = area * yield_per_m2 * soil share * care share
    soil share = min(fertility / required, farm.soil_share_cap)  (D-256)
    care share = 1 - neglect_penalty * (1 - relief * hardiness/5) * skipped days / 100

The soil share is capped: rich land is an edge, not a multiplier, otherwise
the degenerate optimum is the least demanding crop on the best land (OQ-107).

`yield_per_m2` is not set by hand -- the vault derived it from `harvest.rates`
(D-136), and the engine takes it ready. Harvest quality is fertility taken by
the care share: tended land gives what is in it, neglected land gives worse.

**Depletion.** `farm.soil_depletion` for **every** harvest, whatever the crop;
a repeat of the same crop in a row adds `farm.monoculture_penalty` on top
(D-256): monoculture eats the land twice as fast, but rotation is not free
either -- otherwise alternating two crops was a perpetual motion machine.
A restoring crop returns its `restores_fertility` from the data (beans),
fallow recovers by `farm.fallow_recovery` per idle day, credited by elapsed
time on the next action -- the land needs no tick, like sleep.

## Honest simplifications of this version

* **By-product** (straw for spelt) is not given: the share is not set by data,
  and inventing it here is not allowed (D-065);
* **Diseases and the five care parameters** (OQ-098) are reduced to one daily
  round: what the player answers with during care is an open question of the
  pilot screen, and until it closes the round is binary.
"""

from src.engine.farm._base import (  # noqa: F401
    WATER,
    FarmError,
    NoLand,
    NoSeeds,
    NotYours,
    NoWater,
    TooSmall,
    WrongClimate,
    WrongState,
    care_minutes,
    day_hours,
    ripe_at,
)
from src.engine.farm.plot import (  # noqa: F401
    mark,
    merge,
    plow,
    plow_done,
    split,
)
from src.engine.farm.season import (  # noqa: F401
    care,
    harvest,
    sow,
    survey,
    water_need,
)
