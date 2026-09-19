# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: the flyby's rows (D-341) -- what one hull's slider remembers of the
passages the sky offers it, bent round a third world or not, and what an
order keeps of the flyby it flies.

Beside `sim`, below it: the slider (`slider.offers`) asks here for what one
hull is offered at the moment -- direct arcs and flybys together, cut to the
choices -- and the order (`sim.depart`) for the fields a flyby adds to the
course; the tick (`helm`) reads the helm's leg back. The arithmetic is the
sky's (`sky.routes`, `sky.steer_pass`); this module remembers it, runs it
where it cannot stall the server, and writes it down.

**Where the arithmetic runs.** Refining one hull's flybys is seconds of
numpy over hundreds of rows -- Python between every call, the interpreter's
lock held nearly throughout. In a thread beside the event loop that was measured
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
from src.settings import settings
from src.units import (
    HOURS_PER_DAY,
    ROUND_DV,
    SKY_CURVE_MEMO,
    SKY_MEMO_PER_DAY,
)


async def offered(
    constants: Constants,
    world: sky.System,
    target: sky.Body,
    leaving: sky.Body | None,
    r: tuple[float, float],
    v: tuple[float, float],
    t: float,
    *,
    reach: float,
    basis: tuple,
) -> list[sky.Sample]:
    """The slider one hull is offered to a planet (D-341): direct arcs and
    flybys, cut to the choices for engines that give `reach` a day of flight.

    Laid from the hull's own place and velocity at the moment asked -- on its
    parking circle or adrift -- never from a world's centre, so a plan is its
    hull's. Remembered on the hull's `basis` (`slider._basis`), the sky's
    ten-minute bucket and the engines' reach: the console's rereads and the
    order after them find the slider the first reading of the bucket laid,
    and two consoles of one hull asking together wait on one computation.
    """
    key = (
        constants.digest,
        tuple(one.key for one in world.bodies),
        target.key,
        None if leaving is None else leaving.key,
        round(t * SKY_MEMO_PER_DAY),
        basis,
        round(reach, ROUND_DV),
    )
    return await _remembered(
        key,
        partial(
            sky.routes,
            world,
            r,
            v,
            t,
            target,
            course.flyby_grid(constants, sky.search_days(world)),
            leaving=leaving,
            longest=float(constants[R.ORBIT_LONGEST_DAYS]) * HOURS_PER_DAY,
            reach=reach,
            gap=float(constants[R.ORBIT_ROUTE_GAP]),
            floor_radii=float(constants[R.ORBIT_FLYBY_FLOOR_RADII]),
        ),
    )


async def pooled(work: Callable[[], list[sky.Sample]]) -> list[sky.Sample]:
    """A slider's arithmetic in the pool's processes, not remembered: for a
    price that moves by the minute (a meeting in orbit, D-354), where a memo
    would quote the place the hull was at the bucket's first reading. Off the
    event loop and off its interpreter lock both -- a thread holds the lock
    through pure Python."""
    return await asyncio.get_running_loop().run_in_executor(_pool(), work)


async def _remembered(key: tuple, work: Callable[[], list[sky.Sample]]) -> list[sky.Sample]:
    """The memo's answer for `key`, computing it in the pool on a miss -- once,
    however many readers miss it together."""
    hit = _OFFERED.get(key)
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
        _OFFERED[key] = hit
        while len(_OFFERED) > SKY_CURVE_MEMO:
            _OFFERED.popitem(last=False)
        return list(hit)
    return list(await asyncio.shield(pending))


def _pool() -> ProcessPoolExecutor:
    """The refinements' processes, started on the first miss."""
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = ProcessPoolExecutor(max_workers=settings().sky_workers)
    return _EXECUTOR


#: The sliders remembered across commands, the computations under way, and the
#: pool they run in (see `_remembered`).
_OFFERED: OrderedDict[tuple, list[sky.Sample]] = OrderedDict()
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
