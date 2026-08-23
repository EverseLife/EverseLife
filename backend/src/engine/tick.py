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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

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
    journal,
    panel,
    rig,
    road,
    wear,
)
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.job import Job, JobKind
from src.runtime import TICK_STAGES
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


#: The steps of a tick, each a job of its own. Order within a tick is by
#: `after` (minutes after the tick's moment): the land tax goes before the
#: debt collection so a day's tax is not withheld twice; the snapshots go
#: last. Steps of one tick share a dedup key prefix, so two processes
#: scheduling the same tick queue each step once.
Step = Callable[[AsyncSession, datetime], Awaitable[dict[str, Any]]]


async def _chat(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: Live talk is not stored: the delivery buffer is swept by every tick.
    return {"chat_swept": await chat.prune(session, now=now)}


async def _energy(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: Stations work without players: the pool fills by time, and the coal
    #: station burns the delivered coal all that time (D-082).
    return {"energy_produced": await energy.tick_pools(session, current(), now=now)}


async def _rigs(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: The rig does not sleep either: it burns coal, fills the hopper and eats
    #: the vein while the owner is busy elsewhere (D-115). A full hopper stops it.
    return {"rig_mined": await rig.tick_rigs(session, current(), now=now)}


async def _orphans(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: A batch whose job died would otherwise stay "running" for ever, and its
    #: master would count as busy for ever with it (D-211, D-217). The world
    #: sweeps such work away and gives back what went into it.
    return {"batches_abandoned": await craft.sweep_orphans(session)}


async def _wear(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: Gear wears from wearing, not from use (sink S2, D-129).
    return {"gear_worn_out": await wear.daily_gear_wear(session, current(), current_catalog())}


async def _spoil(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: The rotten disappears: spoilage is an honest matter sink (D-119).
    return {"spoiled": await food.sweep_spoiled(session, now=now)}


async def _roads(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: A road without maintenance overgrows and returns to offroad (D-107).
    return {"roads_decayed": await road.decay(session, current())}


async def _houses(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: A house wears out at the pace of what it is built of, and at nothing it
    #: falls (D-218). Timber wants mending twice a year, metal once in a life.
    worn, fallen = await estate.decay(session, current())
    return {"houses_worn": worn, "houses_collapsed": fallen}


async def _land_tax(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: The land tax: the rate is announced at the bioprinter and falls with
    #: every node away from it, so the centre costs more to hold, not only to
    #: buy (D-127, D-220). Charged before the debt collection, so that a
    #: day's tax cannot be withheld twice over.
    return {"land_tax": await estate.levy_land_tax(session, current(), current_catalog())}


async def _debt(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: Overdue debt is repaid by force with a share of the balance (D-063, D-168).
    return {"debt_withheld": await bank.collect(session, current(), now=now)}


async def _sterilize(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: The reserve surplus above the ceiling is burned: the bank's second lever (D-169).
    return {"reserve_burned": await bank.sterilize(session, current())}


async def _metrics(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: The world's daily snapshot. The engine computes it, not the dashboard:
    #: the panel shows the same as the city's in-game summary, and there must be
    #: no second copy of the formulas (D-139, D-124).
    return {"metrics": await metrics.store(session, current(), now=now)}


async def _partitions(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: The journal's months ahead (wave 4): cheap, idempotent, every day.
    return {"partitions": await journal.ensure_partitions(session, now=now)}


async def _cities(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: Each city's snapshot goes into the same table: the panel, the dashboard
    #: and the invariant check are computed by one formula (D-139, D-140).
    return {"cities": await panel.store_daily(session, current(), now=now)}


#: name -> (step, stage): the stage names the delay (`runtime.TICK_STAGES`).
WORLD_STEPS: dict[str, tuple[Step, str]] = {
    "chat": (_chat, "first"),
    "energy": (_energy, "first"),
    "rigs": (_rigs, "first"),
    "orphans": (_orphans, "first"),
}
DAILY_STEPS: dict[str, tuple[Step, str]] = {
    "wear": (_wear, "first"),
    "spoil": (_spoil, "first"),
    "roads": (_roads, "first"),
    "houses": (_houses, "first"),
    "land_tax": (_land_tax, "first"),
    "debt": (_debt, "later"),
    "sterilize": (_sterilize, "later"),
    "metrics": (_metrics, "last"),
    "cities": (_cities, "last"),
    "partitions": (_partitions, "first"),
}
STEPS = {**WORLD_STEPS, **DAILY_STEPS}


async def _fan_out(
    session: AsyncSession, kind: str, steps: dict[str, tuple[Step, str]], now: datetime
) -> None:
    for name, (_, stage) in steps.items():
        await enqueue(
            session,
            JobKind.TICK_STEP,
            now + timedelta(minutes=TICK_STAGES[stage]),
            payload={"step": name, "tick": kind, "at": now.isoformat()},
            dedup_key=f"world.step:{kind}:{name}:{now.isoformat()}",
        )


@handler(JobKind.WORLD_TICK)
async def world_tick(session: AsyncSession, job: Job) -> None:
    """An ordinary tick: schedules its steps and the next tick, nothing more.

    Each step runs as a job of its own (wave 4): the tick used to do them all
    in one transaction, and a slow one held the arrivals and the finishes
    queued behind it.
    """
    now = job.run_at
    await _fan_out(session, "world", WORLD_STEPS, now)
    await events.record(session, EventKind.TICK_RAN, kind_of_tick="world", at=now.isoformat())
    await schedule_next_tick(session, now)


@handler(JobKind.DAILY_TICK)
async def daily_tick(session: AsyncSession, job: Job) -> None:
    """The daily tick: taxes, maintenance, reports, deadlines -- as steps."""
    now = job.run_at
    await _fan_out(session, "daily", DAILY_STEPS, now)
    await events.record(session, EventKind.TICK_RAN, kind_of_tick="daily", at=now.isoformat())
    #: The household meter does not run from here: it has its own period
    #: (`energy.meter_period`) and its own job -- the planet's day and the meter
    #: period need not coincide.
    await schedule_next_day(session, now)


@handler(JobKind.TICK_STEP)
async def tick_step(session: AsyncSession, job: Job) -> None:
    """One step of a tick, in a transaction of its own."""
    name = str(job.payload.get("step"))
    step, _ = STEPS[name]
    now = datetime.fromisoformat(str(job.payload.get("at") or job.run_at.isoformat()))
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    result = await step(session, now)
    await events.record(
        session,
        EventKind.TICK_RAN,
        kind_of_tick=str(job.payload.get("tick")),
        step=name,
        at=now.isoformat(),
        **result,
    )


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
