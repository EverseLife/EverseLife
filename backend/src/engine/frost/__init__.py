# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
"""Warmth: a node is warm or cold, a body carries hours of it (D-231).

Aurora is permafrost and Pyroxis is a furnace, and both planets are unlivable
for the same reason: the ground there does not keep a body alive by itself.
The mechanic is deliberately **binary** -- there are no degrees anywhere in
this module, and no place where "a little warm" could be written:

* **the node is warm** when the planet has no climate of its own at all
  (Terra), when it is a node aboard a ship (life support heats it), or when
  something works in it that heats: a heat plant in the node **or next door**,
  a heater in the node, a lit brazier in the node;
* **the node is cold** otherwise. On the scorching planet it is always cold in
  that sense -- there are no shelters on Pyroxis and never will be (D-230):
  the ship's board and the suit are what a body has there;
* **the body holds a reserve in hours**. In a warm node it comes back
  `frost.warm_rate` times faster than it goes; in a cold one it melts hour by
  hour. Empty reserve -- **frozen**: the body burns stamina on any work at
  `frost.frozen_drain_k` and burns `frost.frozen_stamina` an hour on nothing
  at all. That hour is charged by whatever settles the reserve -- a command as
  readily as the tick -- so acting is no way to outrun the cold. Stamina gone
  while still in the cold -- death, and it is always an explainable one: the
  hours were on the screen the whole time.

## Why the reserve is a pair of columns and not a tick

A body on Terra stands in a warm node for ever, and the world must not write a
row for it every minute. So `body.warmth` holds the hours as of `warmth_at`,
and everything else is arithmetic over the elapsed time -- the way the battery
counts its self-discharge and the plot counts its fallow. The tick sweeps
**only bodies on a planet with a climate**; on Terra the pair is never touched,
and reading it there gives the full reserve however old the stamp is.

## What heat costs

Heat is a round-the-clock drain: `frost.plant_draw` an hour for a plant,
`frost.heater_draw` for a heater, taken from the city pool by `energy.produce`
in the same pass that fills it. An empty pool is a cold city -- that is the
whole price of living on the permafrost, and it is meant to be felt.

## Where this file will split

The file is past the length a file should have, and the roadmap has more coming
to it -- oxygen and the ships' autonomy (D-233). The seam is already visible and
is named here so that the next hand does not have to find it:
**the planet and the node** (`climate_of`, `is_warm`, `heated`, `_standing`),
**the body's reserve** (`settle`, `_advance`, `use_warmer`, `view`) and **the
world's own hours** (`tick_bodies`, `tick_fires`). Splitting is worth doing with the next
thing added, not before: three files with one caller each would be harder to
read than one honest module.

The brazier is the exception the rest rests on: it burns fuel of its own and
asks no pool, so it works where nothing else does -- **including in the frost
itself**. A machine that burns is a machine that works in the cold (there is no
flag for it: the classes that burn are the classes that burn), and without that
rule a frozen city could never be lit again -- the generator that must give the
first heat would itself be standing frozen.
"""

from src.engine.frost._base import (  # noqa: F401
    BRAZIER,
    FROST,
    HEAT,
    HEATER,
    PLANT,
    WARMER,
    FrostError,
    Frozen,
    NotWarmer,
    climate_of,
)
from src.engine.frost.body import (  # noqa: F401
    Spell,
    drain_multiplier,
    limit_of,
    settle,
    use_warmer,
    view,
)
from src.engine.frost.hours import (  # noqa: F401
    tick_bodies,
    tick_fires,
)
from src.engine.frost.warmth import (  # noqa: F401
    burns_own_fuel,
    heat_draw,
    heated,
    is_warm,
    require_working,
    works_here,
)
