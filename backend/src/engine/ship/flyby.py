# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: the flyby's rows (D-341) -- what the slider remembers of the
passages bent round a third world, and what an order keeps of the one it
flies.

Beside `sim`, below it: the slider (`sim.offers`) asks here for the flybys of
the moment and the order (`sim.depart`) for the one it was given and for the
fields a flyby adds to the course; the tick (`helm`) reads the helm's leg
back. The arithmetic is the sky's (`sky.flybys`, `sky.steer_pass`); this
module remembers it, runs it where it cannot stall the server, and writes it
down.

**Where the arithmetic runs.** Refining a world's flybys is seconds of numpy
over hundreds of rows -- Python between every call, the interpreter's lock
held nearly throughout. In a thread beside the event loop that was measured
at a hundred and ninety milliseconds of loop latency at the 99th percentile,
and four at once took four times as long, not one. So it runs in a process of
its own, and two consoles missing the same memo wait on one computation.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from src import sky
from src.constants import Constants
from src.constants import registry as R
from src.engine.ship import course
from src.units import (
    HOURS_PER_DAY,
    ROUND_DV,
    ROUND_HOURS,
    ROUND_TRACE,
    SKY_CURVE_MEMO,
    SKY_MEMO_PER_DAY,
)

#: The processes the refinements run in: one per concurrent world asked
#: about, and no more -- each is a whole core while it works.
_WORKERS = 2


async def offered(
    constants: Constants,
    world: sky.System,
    target: sky.Body,
    leaving: sky.Body | None,
    r: tuple[float, float],
    v: tuple[float, float],
    t: float,
) -> list[sky.Sample]:
    """The slider's flybys (D-341), remembered per sky minute.

    From a parking circle the plan is laid from the world's centre at the
    bucket's own moment, so every hull moored over one world shares it -- what
    differs between them is only the wait, which `offers` adds. From a drift
    the plan is the hull's own.
    """
    moment, here, key = _place_of(constants, target, leaving, r, v, t)
    return await _remembered(
        key,
        partial(
            sky.flybys,
            world,
            here,
            v,
            moment,
            target,
            course.flyby_grid(constants),
            leaving=leaving,
            ceiling=float(constants[R.ORBIT_LONGEST_DAYS]) * HOURS_PER_DAY,
            floor_radii=float(constants[R.ORBIT_FLYBY_FLOOR_RADII]),
        ),
    )


async def given(
    constants: Constants,
    world: sky.System,
    target: sky.Body,
    leaving: sky.Body | None,
    r: tuple[float, float],
    v: tuple[float, float],
    t: float,
    *,
    hours: float,
    via: str,
) -> sky.Sample | None:
    """The one flyby an order names, through `via` at `hours`, whether or not
    the slider would show it now (D-341): the order flies what the console
    quoted, and a pass that still exists -- only a hair dearer than the direct
    arc or than a shorter point since -- is flown rather than refused."""
    moment, here, key = _place_of(constants, target, leaving, r, v, t)
    found = await _remembered(
        (*key, "given", round(hours, ROUND_HOURS), via),
        partial(
            sky.flyby_at,
            world,
            here,
            v,
            moment,
            target,
            hours,
            via,
            leaving=leaving,
            shortest=float(constants[R.ORBIT_SLIDER_FROM_HOURS]) / HOURS_PER_DAY,
            floor_radii=float(constants[R.ORBIT_FLYBY_FLOOR_RADII]),
        ),
    )
    return found[0] if found else None


def _place_of(
    constants: Constants,
    target: sky.Body,
    leaving: sky.Body | None,
    r: tuple[float, float],
    v: tuple[float, float],
    t: float,
) -> tuple[float, tuple[float, float], tuple]:
    """The plan's moment and start, and the memo's key: the sky's ten-minute
    bucket, and for a drift the hull's own state as the wire rounds it."""
    bucket = round(t * SKY_MEMO_PER_DAY)
    if leaving is not None:
        return (
            bucket / SKY_MEMO_PER_DAY,
            (0.0, 0.0),
            (
                constants.digest,
                target.key,
                leaving.key,
                bucket,
            ),
        )
    return (
        t,
        r,
        (
            constants.digest,
            target.key,
            None,
            bucket,
            round(r[0], ROUND_TRACE),
            round(r[1], ROUND_TRACE),
            round(v[0], ROUND_DV),
            round(v[1], ROUND_DV),
        ),
    )


async def _remembered(key: tuple, work: Callable[[], list[sky.Sample]]) -> list[sky.Sample]:
    """The memo's answer for `key`, computing it in the pool on a miss -- once,
    however many readers miss it together."""
    hit = _FLYBYS.get(key)
    if hit is not None:
        return list(hit)
    pending = _PENDING.get(key)
    if pending is None:
        pending = asyncio.get_running_loop().run_in_executor(_pool(), work)
        _PENDING[key] = pending
        try:
            hit = await pending
        finally:
            _PENDING.pop(key, None)
        _FLYBYS[key] = hit
        while len(_FLYBYS) > SKY_CURVE_MEMO:
            _FLYBYS.popitem(last=False)
        return list(hit)
    return list(await asyncio.shield(pending))


def _pool() -> ProcessPoolExecutor:
    """The refinements' processes, started on the first miss."""
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = ProcessPoolExecutor(max_workers=_WORKERS)
    return _EXECUTOR


#: The flybys remembered across commands, the computations under way, and the
#: pool they run in (see `_remembered`).
_FLYBYS: OrderedDict[tuple, list[sky.Sample]] = OrderedDict()
_PENDING: dict[tuple, asyncio.Future[list[sky.Sample]]] = {}
_EXECUTOR: ProcessPoolExecutor | None = None


def order_of(flyby: sky.Pass, *, home: str | None, now: datetime, wait: float) -> dict[str, object]:
    """The flyby on the order (D-341): the world, the periapsis the helm
    corrects toward -- its moment moved by the wait, as the arrival is -- the
    departure's aim, the burn at the pass, the world left (`home`: the one
    moored at, or the one whose hold a drifting hull is in), and the leg the
    helm is on. The periapsis radius and the aim are kept unrounded: the
    corrections close the pass to a hundredth of a unit."""
    return {
        "via": flyby.via,
        "from": home,
        "pass_at": (now + timedelta(days=wait + flyby.at)).isoformat(),
        "rp": flyby.rp,
        "aim": [flyby.aim[0], flyby.aim[1]],
        "burn": flyby.burn,
        "leg": leg_row(sky.Leg()),
    }


def leg_row(leg: sky.Leg) -> dict[str, object]:
    """The helm's place in a flyby, as the order keeps it between ticks."""
    return {"stage": leg.stage, "pending": [leg.pending[0], leg.pending[1]], "mark": leg.mark}


def leg_of(row: dict[str, Any] | None) -> sky.Leg:
    """`leg_row` read back."""
    if not row:
        return sky.Leg()
    pending = row.get("pending") or (0, 0)
    mark = row.get("mark")
    return sky.Leg(
        stage=str(row.get("stage") or sky.Leg().stage),
        pending=(float(pending[0]), float(pending[1])),
        mark=None if mark is None else float(mark),
    )
