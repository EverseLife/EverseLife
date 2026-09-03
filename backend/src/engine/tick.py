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
    automat,
    bank,
    chat,
    craft,
    energy,
    estate,
    events,
    food,
    frost,
    gear,
    journal,
    oxygen,
    panel,
    plates,
    rig,
    road,
    ship,
    wear,
    works,
)
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.job import Job, JobKind
from src.runtime import TICK_STAGES
from src.telemetry import metrics
from src.units import MINUTES_PER_HOUR, ROUND_MASS

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
    #: station burns the delivered coal all that time (D-082). What the city's
    #: heat ate is off the same pass (D-231), so the number is the net change.
    #: And what stands off the grid charges the cells within reach (D-288): a
    #: panel on a hull under way has no pool, only the hull's batteries.
    return {
        "energy_net": await energy.tick_pools(session, current(), now=now),
        "energy_offgrid": round(await energy.tick_offgrid(session, current(), now=now), ROUND_MASS),
    }


async def _automats(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: The automat does not sleep either (D-253): it drinks lubricant, draws
    #: the pool and fills the yard while the owner is busy elsewhere. Any of
    #: the three runs out -- it stands.
    return {"automat_made": await automat.tick_automats(session, current(), now=now)}


async def _rigs(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: The rig does not sleep either: it burns coal, fills the hopper and eats
    #: the vein while the owner is busy elsewhere (D-115). A full hopper stops it.
    return {"rig_mined": await rig.tick_rigs(session, current(), now=now)}


async def _frost(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: The cold does not wait for a login (D-231): the reserve of everybody
    #: standing on a planet with a climate melts by the clock, and a body whose
    #: stamina ran out in the frost dies where it lies. Braziers burn their fuel
    #: in the same pass -- a fire nobody watches is still a fire.
    constants = current()
    dead = await frost.tick_bodies(session, constants, current_catalog(), now=now)
    burnt = await frost.tick_fires(
        session, constants, hours=constants[R.TIME_TICK] / MINUTES_PER_HOUR
    )
    return {"frozen_dead": dead, "brazier_fuel": round(burnt, ROUND_MASS)}


async def _exoskeletons(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: A worn exoskeleton drinks from the batteries in its wearer's hands for
    #: the tick's length (D-268); drained, it lifts nothing until recharged.
    constants = current()
    drunk = await gear.wear_exoskeletons(
        session,
        constants,
        current_catalog(),
        hours=constants[R.TIME_TICK] / MINUTES_PER_HOUR,
        now=now,
    )
    return {"exo_charge": round(drunk, ROUND_MASS)}


async def _oxygen(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: Breathing does not wait for a login either (D-233): a hull under way
    #: makes and spends its air by the clock, and a body left on the black
    #: fields of Pyroxis empties its cylinder whether anybody is watching or
    #: not. Two sweeps, and they never touch the same body: the hull settles
    #: its crew, this settles everybody standing outside one.
    constants = current()
    breathed, lost = await oxygen.tick_ships(session, constants, current_catalog(), now=now)
    outside = await oxygen.tick_bodies(session, constants, current_catalog(), now=now)
    return {"air_breathed": round(breathed, ROUND_MASS), "choked": lost + outside}


async def _sky(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: The sky is flown, not tabled (D-289): every hull under an order is
    #: stepped to now -- the helm, the burn, the pull of five bodies -- and a
    #: coasting one has its stamp moved along so a reading never propagates
    #: weeks. A moored hull runs on its circle and costs nothing here.
    return await ship.helm.tick_sky(session, current(), current_catalog(), now=now)


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
    #: Overdue debt is repaid by force with a share of the balance (D-063, D-168),
    #: and a city that owes the capital pays with a share of its takings (D-285).
    #: People first: their withholding feeds the treasuries the second pass
    #: then takes its share of, and the other order would leave that share for
    #: tomorrow.
    return {
        "debt_withheld": await bank.collect(session, current(), now=now),
        "city_debt_withheld": await bank.collect_from_cities(session, current(), now=now),
    }


async def _works(session: AsyncSession, now: datetime) -> dict[str, Any]:
    #: The reserve surplus above the ceiling burns or feeds the works fund by
    #: the inflation sensor, and the fund posts road orders (D-169, D-248).
    return await works.daily(session, current(), now=now)


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
    "automats": (_automats, "first"),
    "orphans": (_orphans, "first"),
    "frost": (_frost, "first"),
    "exoskeletons": (_exoskeletons, "first"),
    "oxygen": (_oxygen, "first"),
    "sky": (_sky, "first"),
}
DAILY_STEPS: dict[str, tuple[Step, str]] = {
    "wear": (_wear, "first"),
    "spoil": (_spoil, "first"),
    "roads": (_roads, "first"),
    "houses": (_houses, "first"),
    "land_tax": (_land_tax, "first"),
    "debt": (_debt, "later"),
    "works": (_works, "later"),
    "metrics": (_metrics, "last"),
    "cities": (_cities, "last"),
    "partitions": (_partitions, "first"),
}
STEPS = {**WORLD_STEPS, **DAILY_STEPS}
#: The old name of the works step, for step jobs queued before the rename: a
#: pending `sterilize` from yesterday's deploy must run, not KeyError. Lookup
#: only -- not in `DAILY_STEPS`, or the fan-out would queue the step twice.
STEPS["sterilize"] = (_works, "later")


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
    #: A daily step is a metric and is always recorded; a world step that
    #: changed nothing writes no row -- it would be noise in the hottest
    #: table, one per tick per empty step (review 2026-08-23).
    kind = str(job.payload.get("tick"))
    if kind == "daily" or any(result.values()):
        await events.record(
            session, EventKind.TICK_RAN, kind_of_tick=kind, step=name, at=now.isoformat(), **result
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
    #: And so does the ground of Pyroxis (D-197): an eruption is the planet's
    #: weather, on a period of its own, and it queues its own next one.
    await plates.ensure_scheduled(session, now=moment)
