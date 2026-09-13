# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The slider's choices (D-341): which of the passages the sky has priced one
hull is offered.

Three cuts, in this order, over the whole slider of the hull -- direct arcs
and flybys together:

1. **What the engines deliver.** A passage whose delta-v the engines cannot
   give in its own hours is not offered. The engines' reach grows with the
   hours as `engine.ship.course.deliverable` says, so the caller hands in one
   number: what they give in a day of flight.
2. **Only real choices.** Walking from fast to slow, a passage stays only if it
   is cheaper than every faster one: a route both slower and not cheaper than
   another is not a choice. The price then always falls along the slider. An
   hour priced twice -- a direct arc and a flyby -- is one point, the cheaper.
3. **The largest group.** Sorted by hours, the choices part wherever the next
   is more than `gap` times longer than the one before it, and the slider
   offers the group with the most choices -- the faster of two equal ones,
   the short passage being the one the direct slider is laid round (D-317).
   One rule cuts both ends: a far slow group, very long against the rest, and
   a fast group behind a hole in the hours, fuel paid for nothing.

Pure arithmetic over samples: it reads no row and asks the sky nothing.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import TYPE_CHECKING

from src.units import HOURS_PER_DAY

if TYPE_CHECKING:
    from src.sky.plan import Sample


def deliverable(sample: Sample, reach: float) -> bool:
    """Whether the engines give this passage's delta-v in its hours, `reach`
    being what they give in a day of flight."""
    return sample.dv <= reach * sample.hours / HOURS_PER_DAY


def front(samples: Iterable[Sample], *, reach: float) -> list[Sample]:
    """The real choices among the passages the engines deliver, fastest first:
    each cheaper than every one before it."""
    best: dict[float, Sample] = {}
    for one in samples:
        if not deliverable(one, reach):
            continue
        held = best.get(one.hours)
        if held is None or one.dv < held.dv:
            best[one.hours] = one
    kept: list[Sample] = []
    cheapest = math.inf
    for hours in sorted(best):
        one = best[hours]
        if one.dv < cheapest:
            kept.append(one)
            cheapest = one.dv
    return kept


def groups(choices: list[Sample], *, gap: float) -> list[list[Sample]]:
    """The choices, fastest first, parted wherever the next one is more than
    `gap` times longer than the one before it."""
    parted: list[list[Sample]] = []
    for one in choices:
        if parted and one.hours <= gap * parted[-1][-1].hours:
            parted[-1].append(one)
        else:
            parted.append([one])
    return parted


def choices(samples: Iterable[Sample], *, reach: float, gap: float) -> list[Sample]:
    """The slider as offered: the largest group of real choices, fastest first
    -- empty where the engines deliver nothing the sky has."""
    parted = groups(front(samples, reach=reach), gap=gap)
    #: `max` keeps the first of equals: the faster group.
    return max(parted, key=len) if parted else []
