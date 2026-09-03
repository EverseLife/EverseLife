# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
"""Farming by plots (D-118, D-105, D-057, D-293).

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

**Three scales (D-293).** A sown bed carries moisture, health and growth,
none shown as a number, all written as of a stamp and moved by the clock in
`life.py`: moisture leaves as a share of what is there (`farm.dry_rate` at
the culture's own drinking pace, in the heat, under the rain, by the river);
health falls in proportion to how far the moisture sits outside the culture's
band (`farm.stress_per_point`, softened by hardiness -- D-261) and heals
inside it; growth adds its nominal share of the cycle scaled by health and by
a feeding's boost. Nought health is death: the bed goes back to fallow, the
seed is lost, the land pays the cycle's depletion. Full growth is ripeness:
the stage, never a date.

**Care.** No round: actions, each in person (D-211) and each for
`farm.plot_overhead + farm.care_time_per_m2 * area` minutes of busy hands.
A watering takes the bed to a target, and the water is the difference
(`farm.water_per_m2` a metre from dry to full) -- from a river, or carried
(D-126). A feeding is what the culture's table says it is in this stage:
a boost, a burn, or a bed run to leaf.

**Agronomy is a text.** The norms are read in the Library and remembered
into the "knowledge" tab (`text.py`); nothing in the survey changes with
knowledge, and the survey shows the same signs to everybody (D-057).

**Climate gate (D-261).** Sowing checks the place: the node's daily
temperature band (mean from exploration, swing from `planet.temp_swing`)
must fit the culture's `requires.temp` whole, and the day's light
(`engine/climate.py`: 3 in the open, less under woods and buildings) must
reach `requires.light`. A node without a temperature record -- old ones, a
ship's hydroponics bay -- carries no gate.

**Harvest.** "Proportional to area, fertility, health and cultivar strength":

    yield = area * yield_per_m2 * soil share * health/100 * leaf share * strength/100
    soil share = min(fertility / required, farm.soil_share_cap)  (D-256)
    leaf share = 1 - farm.overfeed_yield_penalty * repeated feedings

The soil share is capped: rich land is an edge, not a multiplier, otherwise
the degenerate optimum is the least demanding crop on the best land (OQ-107).

`yield_per_m2` is not set by hand -- the vault derived it from `harvest.rates`
and the actions the model asks for (D-136, D-293), and the engine takes it
ready. Harvest quality is fertility taken by the health share.

**Depletion.** `farm.soil_depletion` for **every** harvest, whatever the crop;
a repeat of the same crop in a row adds `farm.monoculture_penalty` on top
(D-256): monoculture eats the land twice as fast, but rotation is not free
either -- otherwise alternating two crops was a perpetual motion machine.
A restoring crop returns its `restores_fertility` from the data (beans),
fallow recovers by `farm.fallow_recovery` per idle day, credited by elapsed
time on the next action -- the land needs no tick, like sleep. A bed that
died pays the depletion too: it fed the plant all the same.

## Honest simplifications of this version

* **By-product** (straw for spelt) is not given: the share is not set by data,
  and inventing it here is not allowed (D-065);
* **Weeding, thinning and disease** are the next waves of D-293: the three
  scales are in place, the actions that read `density_risk`,
  `disease_risk` and `farm.disease_spread` are not.
"""

from src.engine.farm._base import (  # noqa: F401
    FERTILIZER,
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
    plow_banked,
    plow_minutes,
    plow_paused,
    plow_progress_minutes,
)
from src.engine.farm.care import care_done, feed, water  # noqa: F401
from src.engine.farm.life import (  # noqa: F401
    HEALTH_WORDS,
    RIPE,
    SPROUT,
    STAGES,
    Life,
    Norms,
    Weather,
    advance,
    dry_rate,
    health_word,
    norms,
    stage_of,
    symptoms,
)
from src.engine.farm.plot import (  # noqa: F401
    fertilize,
    mark,
    merge,
    plow,
    plow_done,
    plow_pause,
    plow_reset,
    split,
)
from src.engine.farm.season import harvest, sow, survey  # noqa: F401
from src.engine.farm.settle import peek, settle, tick_plots  # noqa: F401
from src.engine.farm.text import (  # noqa: F401
    care_text,
    read_care,
    remember_care,
    remembered,
)
