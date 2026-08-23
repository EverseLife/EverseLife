# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The world tick.

The world lives without players. The tick is not "a cron poking a function"
but an ordinary journal job that **queues the next itself** and therefore can
neither get lost nor double.

Two rhythms (20-systems/01-time-model):

* **tick** -- `time.tick` minutes: advancing long-running actions, spoilage,
  wear by time, timers firing;
* **daily tick** -- taxes, building maintenance, reports, deadlines.

The handlers below are still empty where the system does not exist yet. That
is deliberate: the time skeleton must work before a single mechanic appears --
batches, caravans, growth and daily write-offs rest on it (07-implementation-map).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import current, current_catalog
from src.constants import registry as R
from src.engine import (
    bank,
    chat,
    craft,
    energy,
    estate,
    events,
    food,
    panel,
    rig,
    road,
    wear,
)
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.job import Job, JobKind
from src.telemetry import metrics

log = logging.getLogger(__name__)


def tick_period() -> timedelta:
    """Tick length. The number comes from constants (D-065), not code."""
    return timedelta(minutes=current()[R.TIME_TICK])


def _slot(moment: datetime, period: timedelta) -> int:
    """The interval number since the epoch -- also the idempotency key.

    Two processes deciding to queue the tick at once will queue a job with one
    key, and it will be one.
    """
    return int(moment.timestamp() // period.total_seconds())


async def schedule_next_tick(session: AsyncSession, after: datetime) -> None:
    period = tick_period()
    run_at = after + period
    await enqueue(
        session,
        JobKind.WORLD_TICK,
        run_at,
        dedup_key=f"world.tick:{_slot(run_at, period)}",
    )


async def schedule_next_day(session: AsyncSession, after: datetime) -> None:
    period = timedelta(days=1)
    run_at = after + period
    await enqueue(
        session,
        JobKind.DAILY_TICK,
        run_at,
        dedup_key=f"world.daily:{_slot(run_at, period)}",
    )


@handler(JobKind.WORLD_TICK)
async def world_tick(session: AsyncSession, job: Job) -> None:
    """An ordinary tick. Everything it does is done in its transaction."""
    now = job.run_at
    #: Live talk is not stored: the delivery buffer is swept by every tick.
    swept = await chat.prune(session, now=now)
    #: Stations work without players: the pool fills by time, and the coal
    #: station burns the delivered coal all that time (D-082).
    produced = await energy.tick_pools(session, current(), now=now)
    #: The rig does not sleep either: it burns coal, fills the hopper and eats
    #: the vein while the owner is busy elsewhere (D-115). A full hopper stops it.
    mined = await rig.tick_rigs(session, current(), now=now)
    #: A batch whose job died would otherwise stay "running" for ever, and its
    #: master would count as busy for ever with it (D-211, D-217). The world
    #: sweeps such work away and gives back what went into it.
    abandoned = await craft.sweep_orphans(session)
    await events.record(
        session,
        EventKind.TICK_RAN,
        kind_of_tick="world",
        at=now.isoformat(),
        chat_swept=swept,
        energy_produced=produced,
        rig_mined=mined,
        batches_abandoned=abandoned,
    )
    #: To come here: wear by time, order expiry. Batches do not wait for the
    #: tick -- each arrives by its own job at its own time.
    await schedule_next_tick(session, now)


@handler(JobKind.DAILY_TICK)
async def daily_tick(session: AsyncSession, job: Job) -> None:
    """The daily tick: taxes, maintenance, reports, deadlines."""
    now = job.run_at
    #: Gear wears from wearing, not from use (sink S2, D-129).
    gone = await wear.daily_gear_wear(session, current(), current_catalog())
    #: The rotten disappears: spoilage is an honest matter sink (D-119).
    rotten = await food.sweep_spoiled(session, now=now)
    #: A road without maintenance overgrows and returns to offroad (D-107).
    overgrown = await road.decay(session, current())
    #: A house wears out at the pace of what it is built of, and at nothing it
    #: falls (D-218). Timber wants mending twice a year, metal once in a life.
    houses_worn, houses_fallen = await estate.decay(session, current())
    #: The land tax: the rate is announced at the bioprinter and falls with
    #: every node away from it, so the centre costs more to hold, not only to
    #: buy (D-127, D-220). Charged before the debt collection below, so that a
    #: day's tax cannot be withheld twice over.
    land_tax = await estate.levy_land_tax(session, current(), current_catalog())
    #: Overdue debt is repaid by force with a share of the balance (D-063, D-168).
    withheld = await bank.collect(session, current(), now=now)
    #: The reserve surplus above the ceiling is burned: the bank's second lever (D-169).
    burned = await bank.sterilize(session, current())
    #: The world's daily snapshot. The engine computes it, not the dashboard:
    #: the panel shows the same as the city's in-game summary, and there must be
    #: no second copy of the formulas (D-139, D-124).
    measurements = await metrics.store(session, current(), now=now)
    #: Each city's snapshot goes into the same table: the panel, the dashboard
    #: and the invariant check are computed by one formula (D-139, D-140).
    city_count = await panel.store_daily(session, current(), now=now)
    await events.record(
        session,
        EventKind.TICK_RAN,
        kind_of_tick="daily",
        at=now.isoformat(),
        gear_worn_out=gone,
        spoiled=rotten,
        roads_decayed=overgrown,
        houses_worn=houses_worn,
        houses_collapsed=houses_fallen,
        land_tax=land_tax,
        debt_withheld=withheld,
        reserve_burned=burned,
        metrics=measurements,
        cities=city_count,
    )
    #: The household meter does not run from here: it has its own period
    #: (`energy.meter_period`) and its own job -- the planet's day and the meter
    #: period need not coincide. To come here: the city trade summary.
    await schedule_next_day(session, now)


async def ensure_scheduled(session: AsyncSession, now: datetime | None = None) -> None:
    """Make sure the world clock runs. Called at process start.

    The household meter (D-149) is started **separately** -- `utility.ensure_scheduled`:
    it has its own period, and the world clock need not know who else ticks nearby.
    """
    moment = now or datetime.now(UTC)
    await enqueue(
        session,
        JobKind.WORLD_TICK,
        moment,
        dedup_key=f"world.tick:{_slot(moment, tick_period())}",
    )
    await enqueue(
        session,
        JobKind.DAILY_TICK,
        moment,
        dedup_key=f"world.daily:{_slot(moment, timedelta(days=1))}",
    )
    #: The key-rate review runs at its own rhythm (D-167): monetary policy has
    #: its own period, and the world clock need not know it.

    await bank.schedule_review(session, current(), after=moment)
