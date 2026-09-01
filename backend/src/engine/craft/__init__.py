# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Craft: batch, quality, losses (D-092, D-133).

Five conditions at once, all mandatory: knowledge, machine, tool, inputs, place
(20-systems/03-crafting). A batch is started in person and runs offline; the
input is written off at once, the product appears on schedule.

## Where each formula came from

Numbers are set by the vault, the order of steps is the engine's business
(vault CLAUDE.md). Below is the derivation of each formula so it can be checked
against D-092 and D-133 rather than taken on faith.

**Batch time.** `craft.time_per_unit` is "base batch time per unit of output",
`craft.time_growth_per_level` is "how many times longer a product takes with
each processing level deeper". The vault has already folded both into
`labor_hours`: a product's labour equals its own processing time plus the
labour of its inputs. So the own time is obtained by subtraction and **is not
re-derived in code**:

    step(product) = labor_hours(product) - sum(amounts[j] * labor_hours(j))

An operation without a recipe has its own time set directly --
`hours_per_unit[output]`.

**Machine speed.** `craft.station_speed_k` is "time multiplier from machine
quality; a broken anvil works slowly". So the worst machine works at the upper
bound of the multiplier, the best at the lower:

    k = max - (max - min) * machine_quality / scale

**Quality ceiling.** The lesser of machine and tool quality: the weakest link
limits (15-quality). What is absent does not limit: a "By hand" recipe without a
tool is bounded only by the raw material.

**Approach to the ceiling.** For an assembly it is set by inputs alone, for a
mix by inputs and proportion accuracy, with weights `quality.material_weight`
and `quality.ratio_weight` (D-092).

**Optimal proportion for a mix.** "Poor ore needs more coal and flux, pure ore
less". Amounts from `recipes.json` are the norm for ordinary raw material, i.e.
for the middle of the quality scale; deviation from it is symmetric:

    optimum[additive] = amounts[additive] * (1 + (middle - base_quality) / scale)

The base is the recipe's first input. A poor base needs up to one and a half
norms of additives, an excellent one down to half.

**Losses and spread.** `craft.waste_share` for correct work,
`craft.waste_bad_ratio` for a miss; `quality.spread_good_ratio` and
`quality.spread_bad_ratio` likewise. There is no "hit / miss" threshold in the
vault, and inventing one here is not allowed: both quantities follow accuracy
continuously.

**Craft premium.** `quality.hand_craft_bonus` is "up to +10 for hitting the
proportions exactly". Only for a mix: an assembly has no proportions at all, and
the premium there would be a bonus out of thin air.

Not one number beyond the vault appeared here. If a quantity is missing, it is
added to `data/constants.yaml`, not to code (D-065).

## What is not here yet

* **Dishes by roles** (`roles: true`) -- arrive with cooking on E2 (D-119,
  D-128) together with `cook.*`;
* **The unattended automat** lives in `engine/automat.py` (D-253): the
  D-035 attended mode is gone -- one machine, one meaning;
* **Invention, repair and recycling** -- separate registry actions with their
  own constants.

A package: one module per section of the old file; this file re-exports
the names so `from src.engine import craft` reads as before.
"""

from src.engine.craft._base import (  # noqa: F401
    BENCHLESS,
    BLANK,
    CARRIER,
    HANDS,
    Busy,
    CraftError,
    CutOff,
    NoLibrary,
    NoStation,
    NoStrength,
    NotEnough,
    NotIngredient,
    NotLearned,
    NoTool,
    Plan,
    Procedure,
    TooBig,
    Unmakeable,
    _Pick,
    _Ready,
    blank_of,
    carrier_names,
)
from src.engine.craft._internal import (  # noqa: F401
    _base_quality,
    _knows,
    _material_quality,
    _num,
    _occupy,
    _pick,
    _pick_station,
    _pieces,
    _prepare,
    _prepare_write,
    _release,
    _seconds,
    _station_item,
    _stock,
    _tiers_by,
    _tool_items,
    _wear_station,
    write_seconds,
)
from src.engine.craft.batch import (  # noqa: F401
    UTENSILS,
    _finish_make,
    _finish_recycle,
    _finish_repair,
    #: The pair goes out together: the spend is only safe under the lock.
    _lock_body,
    _pay_copy,
    _target,
    _work_on,
    cook,
    copy_recipe,
    finish,
    plan,
    recycle,
    repair,
    start,
)
from src.engine.craft.invention import (  # noqa: F401
    Invention,
    _match,
    invent,
)
from src.engine.craft.knowledge_carriers import (  # noqa: F401
    read_carrier,
    wipe_carrier,
)
from src.engine.craft.method_of_making import (  # noqa: F401
    _from_operation,
    _from_recipe,
    batch_minutes,
    procedure,
    step_hours,
)
from src.engine.craft.quality import (  # noqa: F401
    forecast_quality,
    optimal_amounts,
    quality_cap,
    ratio_accuracy,
    spread_of,
    waste_share,
)
from src.engine.craft.queue import (  # noqa: F401
    _abandon,
    _batch_key,
    _launch,
    _run,
    freeze,
    present,
    running,
    sweep_orphans,
    waiting,
    wake,
    wake_node,
)
