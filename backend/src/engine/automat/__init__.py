# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
"""The automat: a station that works without the player (D-253, revising D-035).

The third transition from labour to capital, after the rig (D-115) and the
field automaton (D-120), and built by the same creed: the machine loses to a
human on every measure but one -- it does not sleep.

| | Human | Automat |
|---|---|---|
| Speed | the recipe's own hours | `auto.speed_share` of that |
| Quality | by skill and inputs | not above `auto.quality_cap` |
| Needs | food and sleep | lubricant, energy, maintenance |
| Presence | constantly | only to program and to haul |

Craft remains the way to get **good things**, the automat the way to get
**a lot of average** -- word for word the rig's bargain.

## What it executes

One machine, one recipe (chains between machines are the node editor's
business, D-253 wave 5). The recipe is loaded **out of the owner's own
knowledge** (D-068, D-209): the machine is not a free library. Which recipes
a machine may take at all is the vault's, not code's:

* `auto.covers` -- station -> automat: the assembler stands in for benches,
  forges and workshops, the furnace for the smelters, the reactor for the
  chemistry. A station outside the table -- the hearth, the mint, the
  shipyards, «Руками» -- is outside automation by construction;
* `auto.barred_inputs` -- the pyroxite tier waits for its own station (OQ-106);
* stations themselves are never programmed: a station is a build, and its
  scale is set by hand (D-223).

## How it works

The worker tick advances every automat by wall time, like the rigs. An hour
of work needs `auto.lube_per_hour` of lubricant from the vessels standing in
the node (D-230: a liquid lives in a vessel; hauling lubricant is the coal
run of factories) and `auto.energy_per_hour` from the city pool, billed to
the owner at the tariff (D-135: whoever burns pays) -- or drawn from the
node's own batteries where no grid reaches (D-071). Inputs come off the
node's yard and the vessels in it; outputs land there too, a liquid poured
into vessels and **waiting in the backlog** while no vessel has room -- the
well does not spill for a forgotten canister, and neither does the reactor.

The backlog is time, not matter: inputs are consumed at payout, so work
never strands materials inside the machine. Wear runs by the clock whether
it works or stands -- an abandoned automat falls apart (`auto.wear_per_day`).
"""

from src.engine.automat._base import (  # noqa: F401
    AUTOMAT,
    LUBE,
    AutomatError,
    BarredInput,
    NoStationBuilds,
    NotAnAutomat,
    NotCovered,
    RecipeUnknown,
    SelfLink,
    of_item,
)
from src.engine.automat.board import (  # noqa: F401
    program,
    stop,
    view,
)
from src.engine.automat.run import (  # noqa: F401
    advance,
    tick_automats,
)
from src.engine.automat.wire import (  # noqa: F401
    _chain_order,
    link,
    unlink,
)
