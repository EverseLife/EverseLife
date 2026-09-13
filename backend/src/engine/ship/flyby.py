# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: the flyby's rows (D-341) -- what the slider remembers of the
passages bent round a third world, and what an order keeps of the one it
flies.

Beside `sim`, below it: the slider (`sim.offers`) asks here for the flybys of
the moment and the order (`sim.depart`) for the fields a flyby adds to the
course; the tick (`helm`) reads the helm's leg back. The arithmetic is the
sky's (`sky.flybys`, `sky.steer_pass`); this module only remembers it and
writes it down.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src import sky
from src.constants import Constants
from src.constants import registry as R
from src.engine.ship import course
from src.engine.ship._base import is_orbit
from src.models.ship import Ship
from src.models.world import Node
from src.units import (
    HOURS_PER_DAY,
    ROUND_DV,
    ROUND_TRACE,
    SKY_CURVE_MEMO,
    SKY_MEMO_PER_DAY,
)


def stamp(moment: datetime) -> str:
    return moment.isoformat()


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
    the plan is the hull's own. Solved off the loop: a cold world takes
    seconds.
    """
    bucket = round(t * SKY_MEMO_PER_DAY)
    moment = bucket / SKY_MEMO_PER_DAY
    here = (0.0, 0.0) if leaving is not None else r
    key = (
        constants.digest,
        target.key,
        None if leaving is None else leaving.key,
        bucket,
        *(
            ()
            if leaving is not None
            else (
                round(r[0], ROUND_TRACE),
                round(r[1], ROUND_TRACE),
                round(v[0], ROUND_DV),
                round(v[1], ROUND_DV),
            )
        ),
    )
    hit = _FLYBYS.get(key)
    if hit is None:
        hit = await asyncio.to_thread(
            sky.flybys,
            world,
            here,
            v,
            moment if leaving is not None else t,
            target,
            course.flyby_grid(constants),
            leaving=leaving,
            ceiling=float(constants[R.ORBIT_LONGEST_DAYS]) * HOURS_PER_DAY,
            floor_radii=float(constants[R.ORBIT_FLYBY_FLOOR_RADII]),
        )
        _FLYBYS[key] = hit
        while len(_FLYBYS) > SKY_CURVE_MEMO:
            _FLYBYS.popitem(last=False)
    return list(hit)


#: The flybys remembered across commands (see `offered`).
_FLYBYS: OrderedDict[tuple, list[sky.Sample]] = OrderedDict()


async def order_of(
    session: AsyncSession, ship: Ship, flyby: sky.Pass, *, now: datetime, wait: float
) -> dict[str, object]:
    """The flyby on the order (D-341): the world, the periapsis the helm
    corrects toward -- its moment moved by the wait, as the arrival is -- the
    departure's aim, the burn at the pass, the world left, and the leg the
    helm is on. The periapsis radius and the aim are kept unrounded: the
    corrections close the pass to a hundredth of a unit."""
    home = None
    if ship.docked_node_id is not None:
        moored = await session.get(Node, ship.docked_node_id)
        home = None if moored is None or not is_orbit(moored) else moored.planet.value
    return {
        "via": flyby.via,
        "from": home,
        "pass_at": stamp(now + timedelta(days=wait + flyby.at)),
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
