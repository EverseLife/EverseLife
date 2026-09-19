# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A meeting in orbit caught as the other hull is given an order (D-354).

One of the race files (see `test_races.py` for the family's method). The
contended row is the **target's**: the tick that brings a chaser to rest
beside it writes it -- the pair's speed, its coast, its loss (`hold.begin`)
-- and an order given to the target in the same second writes it too. The
tick takes the row `FOR UPDATE SKIP LOCKED` before it reads whether the
target may still be met (`helm._fly`): a target under somebody's hand this
second is left for the next minute.

Without that lock the tick read a target that was no longer to be met --
its order written and not yet committed -- came to rest beside it, and
wrote the pair over the order once it committed: a hull held on to one under
an order, with a forecast it has no business having. The race here makes the
order go first and hold the row; the tick must walk past it, not wait on it.
"""

from __future__ import annotations

import asyncio
import math
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _until_blocked_by
from ship_kit import PARK_HEADING, _hull, _planet, _port
from src.api.commands.transport import _ship_fly
from src.constants import Catalog, Constants
from src.engine import ship
from src.engine.ship import hold
from src.models.ship import Ship
from src.models.world import Planet

#: Fuel for the crossing the target is ordered onto, and for the meeting.
FUEL = 5000.0


async def test_a_target_ordered_away_as_it_is_met_is_left_to_its_order(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    home = await _port(session, name="Космодром столицы")
    await _port(session, name="Порт Авроры", planet=Planet.AURORA)
    await _planet(session, Planet.AURORA)
    chaser, chaser_owner = await _hull(
        session, constants, catalog, home, fuel=FUEL, heading=PARK_HEADING
    )
    #: A hair behind on the same circle: the chaser comes to rest on the
    #: first minute of its order.
    other, other_owner = await _hull(
        session, constants, catalog, home, fuel=FUEL, heading=PARK_HEADING - 1e-4
    )
    since = max(chaser.sky_at, other.sky_at) + timedelta(minutes=1)
    forecast = await ship.forecast(session, constants, catalog, chaser, other, now=since)
    assert forecast["samples"], forecast.get("why")
    await ship.fly(
        session,
        constants,
        catalog,
        chaser_owner,
        chaser,
        other,
        hours=forecast["samples"][0]["hours"],
        now=since,
    )
    chaser_id, other_id = chaser.id, other.id
    state = {"identity_id": other_owner.identity_id}
    await session.commit()

    held = asyncio.Event()
    waited: list[bool] = []
    ticks: list[asyncio.Future[dict]] = []

    async def ordering() -> dict:
        async with factory() as db, db.begin():
            answer = await _ship_fly(state, db, {"planet": Planet.AURORA.value})
            held.set()
            waited.append(await _until_blocked_by(factory, db, unless=ticks[0]))
            return answer

    async def ticking() -> dict:
        await held.wait()
        async with factory() as db, db.begin():
            return await ship.helm.tick_sky(
                db, constants, catalog, now=since + timedelta(minutes=2)
            )

    ticks.append(asyncio.ensure_future(ticking()))
    ordered, ticked = await asyncio.gather(ordering(), ticks[0])

    assert waited == [False], "the tick waited on the target's row instead of walking past"
    assert ordered is not None and ticked is not None
    async with factory() as db:
        target = await db.get(Ship, other_id)
        follower = await db.get(Ship, chaser_id)
        assert target is not None and follower is not None
        assert target.course and target.course.get("planet") == Planet.AURORA.value
        assert target.forecast is None, "an ordered hull carries no coast"
        assert follower.held_ship_id is None, "nobody holds on to a hull under an order"
        assert (await db.execute(hold.orphaned_holds())).all() == []
        assert math.isfinite(float(follower.sky_x))
