# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The field automaton (D-120, D-121, D-253, D-296, D-297, D-339).

A machine standing in a yard walks its owner's programme over the plots given
to it: **plough**, **sow** a culture, **hold the moisture** at a setpoint,
**feed** a fertilizer in a stage, **weed** every so many days, **thin**,
**harvest**, lie **fallow** so many days -- round and round, at most
`agro.program_steps` lines. The four actions hold the cursor until they are
done on every plot; the four setpoints take a line and no time and hold their
whole season, from the harvest before them to the harvest after.

It sees the moisture, the stage and the calendar, and nothing else: health,
weeds and the signs of a pest are the farmer's eyes (D-296, D-299). What a
hand did on its plots is done -- watered is wetter, fed in a stage is fed,
weeded restarts the count. It takes seeds and fertilizer from the storages
its owner names and reaps into a third -- itself, a bunker, by default of
nothing better -- at `agro.yield_share` of a hand's harvest and never above
`agro.quality_cap`, with no selection, so its cultivar degrades (D-067).

It is an enterprise, not free food: energy by the hour billed to the owner,
the family's lubricant from the yard's vessels, wear by the clock. Short of
energy or lubricant it stands, and the beds live on without it.

Modules, each asking only those below it (pinned by import-linter):
`_base` (vocabulary, programme, guards) <- `hands` (one action on one plot)
<- `run` (the advance and the tick) <- `board` (programme, stop, view).
"""

from __future__ import annotations

from src.engine.agro._base import (  # noqa: F401
    ACTIONS,
    COMMANDS,
    FIELD_AUTOMAT,
    SETPOINTS,
    TROUBLES,
    AgroError,
    BadPlot,
    BadProgram,
    BadStore,
    NotAFieldAutomat,
    in_force,
    of_item,
    parse,
)
from src.engine.agro.board import program, stop, view  # noqa: F401
from src.engine.agro.run import advance, tick_fields  # noqa: F401
