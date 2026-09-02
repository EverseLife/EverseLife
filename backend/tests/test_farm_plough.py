# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The plough: a work of hours that pauses with its progress kept (D-211, D-277).

Split from `test_farm.py` at the 800-line bar (CLAUDE.md). Checked is what
the pause is built for: the bank of minutes, the remainder on resuming, the
reset as a decision of its own, and the two orders of the race between a
pause and the worker finishing the same strip.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from farm_kit import _farmstead
from src.constants import Constants
from src.constants import registry as R
from src.engine import farm, jobs, occupation
from src.engine.farm import plot as plot_
from src.models.farm import Plot, PlotState
from src.models.identity import Body
from src.models.job import Job, JobKind, JobState


async def test_ploughing_goes_by_job(
    factory: async_sessionmaker[AsyncSession], constants: Constants
) -> None:
    async with factory() as session, session.begin():
        _, _, body = await _farmstead(session)
        plot = await farm.plow(
            session,
            constants,
            body,
            await farm.mark(session, constants, body, name="грядка", area=10),
        )
        assert plot.state is PlotState.PLOWING
        plot_id = plot.id

    job = await jobs.run_one(factory, now=datetime.now(UTC) + timedelta(hours=1))
    assert job is not None and job.kind == "farm.plow"

    async with factory() as session:
        plot = await session.get(Plot, plot_id)
        assert plot.state is PlotState.PLOWED


async def test_the_plough_pauses_with_its_progress_kept(
    factory: async_sessionmaker[AsyncSession], constants: Constants
) -> None:
    """Pausing banks the minutes ploughed; taking it up again runs the remainder (D-277).

    The strip stays under the plough, nobody at it: the hands are free, the
    job is cancelled, and `plow` on the paused strip queues a run for what is
    left rather than for the whole.
    """
    started = datetime.now(UTC)
    whole = constants[R.FARM_PLOW_TIME_PER_M2] * 10.0
    async with factory() as session, session.begin():
        _, _, body = await _farmstead(session)
        plot = await farm.plow(
            session,
            constants,
            body,
            await farm.mark(session, constants, body, name="грядка", area=10),
            now=started,
        )
        plot_id, body_id = plot.id, body.id

    paused = started + timedelta(minutes=3)
    async with factory() as session, session.begin():
        body = await session.get(Body, body_id)
        assert body is not None
        #: Named the strip under the plough: the same as unnamed.
        plot = await session.get(Plot, plot_id)
        strip = await farm.plow_pause(session, constants, body, plot=plot, now=paused)
        assert strip.state is PlotState.PLOWING
        assert farm.plow_paused(strip)
        assert float(strip.plow_done_minutes) == pytest.approx(3.0)
        assert await occupation.current(session, body) is None
        #: Twice is once: the second pause has nothing to stop.
        with pytest.raises(farm.WrongState):
            await farm.plow_pause(session, constants, body)

    #: The worker finds nothing to finish: the job is cancelled, not pending.
    assert await jobs.run_one(factory, now=paused + timedelta(days=1)) is None

    resumed = paused + timedelta(hours=2)
    async with factory() as session, session.begin():
        body = await session.get(Body, body_id)
        plot = await session.get(Plot, plot_id)
        assert body is not None and plot is not None
        await farm.plow(session, constants, body, plot, now=resumed)
        assert plot.plow_since == resumed
        job = (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.FARM_PLOW.value, Job.state == JobState.PENDING.value
                )
            )
        ).scalar_one()
        #: The remainder alone: three minutes of the whole are already done.
        assert job.run_at == resumed + timedelta(minutes=whole - 3.0)

    assert await jobs.run_one(factory, now=resumed + timedelta(minutes=whole)) is not None
    async with factory() as session:
        plot = await session.get(Plot, plot_id)
        assert plot is not None and plot.state is PlotState.PLOWED
        assert plot.plow_since is None and float(plot.plow_done_minutes) == 0


async def test_the_plough_is_dropped_only_from_a_pause(
    session: AsyncSession, constants: Constants
) -> None:
    """A reset is a separate act, two presses from the work (D-277): a running
    plough is refused and told to pause first; a paused one goes back to
    fallow with nothing kept."""
    started = datetime.now(UTC)
    _, _, body = await _farmstead(session)
    plot = await farm.mark(session, constants, body, name="грядка", area=10)
    await farm.plow(session, constants, body, plot, now=started)

    with pytest.raises(farm.WrongState):
        await farm.plow_reset(session, body, plot)
    assert plot.state is PlotState.PLOWING and plot.plow_since is not None

    later = started + timedelta(minutes=5)
    await farm.plow_pause(session, constants, body, plot=plot, now=later)
    strip = await farm.plow_reset(session, body, plot, now=later)
    assert strip.state is PlotState.IDLE
    assert strip.idle_since == later
    assert float(strip.plow_done_minutes) == 0 and strip.plow_since is None
    assert await occupation.current(session, body) is None

    #: Fallow is not under the plough: nothing to drop.
    with pytest.raises(farm.WrongState):
        await farm.plow_reset(session, body, plot)


async def test_a_pause_banks_no_more_than_the_whole(
    session: AsyncSession, constants: Constants
) -> None:
    """Paused after the job's hour and before the worker's visit: a finished
    plough waiting, not a surplus over the norm."""
    started = datetime.now(UTC)
    _, _, body = await _farmstead(session)
    plot = await farm.mark(session, constants, body, name="грядка", area=10)
    await farm.plow(session, constants, body, plot, now=started)

    whole = farm.plow_minutes(constants, plot)
    strip = await farm.plow_pause(
        session, constants, body, plot=plot, now=started + timedelta(minutes=whole * 3)
    )
    assert float(strip.plow_done_minutes) == pytest.approx(whole)


async def test_the_plough_pauses_only_on_its_own_strip(
    session: AsyncSession, constants: Constants
) -> None:
    """Naming another strip does not pause the plough on this one."""
    _, _, body = await _farmstead(session)
    under = await farm.mark(session, constants, body, name="под плугом", area=10)
    other = await farm.mark(session, constants, body, name="соседняя", area=10)
    await farm.plow(session, constants, body, under)

    with pytest.raises(farm.WrongState):
        await farm.plow_pause(session, constants, body, plot=other)
    assert under.plow_since is not None


async def _plough_under_way(
    factory: async_sessionmaker[AsyncSession], constants: Constants, started: datetime
) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as session, session.begin():
        _, _, body = await _farmstead(session)
        plot = await farm.plow(
            session,
            constants,
            body,
            await farm.mark(session, constants, body, name="грядка", area=10),
            now=started,
        )
        return plot.id, body.id


async def _later[T](delay: float, run: Callable[[], Awaitable[T]]) -> T:
    """The second party to a race: in after the first has taken its lock."""
    await asyncio.sleep(delay)
    return await run()


async def _pause_from(
    factory: async_sessionmaker[AsyncSession], body_id: uuid.UUID, constants: Constants
) -> bool:
    async with factory() as db, db.begin():
        body = await db.get(Body, body_id)
        assert body is not None
        with contextlib.suppress(farm.WrongState):
            await farm.plow_pause(db, constants, body)
            return True
        return False


async def _judged(
    factory: async_sessionmaker[AsyncSession], plot_id: uuid.UUID
) -> tuple[Plot, Job]:
    async with factory() as db:
        plot = await db.get(Plot, plot_id)
        assert plot is not None
        job = (
            await db.execute(select(Job).where(Job.kind == JobKind.FARM_PLOW.value))
        ).scalar_one()
        return plot, job


async def test_race_the_worker_first_finishes_and_the_pause_finds_nothing(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plough is finished or paused, never both (CLAUDE.md: a state under lock).

    The worker claims the job and holds it across a pause; the pause, in
    after it, waits at the job's lock and then rereads a finished row --
    Postgres rejudges `state = pending` on the new version -- and refuses.
    """
    started = datetime.now(UTC)
    plot_id, body_id = await _plough_under_way(factory, constants, started)
    _slow(monkeypatch, jobs, "_claim")

    done, stopped = await asyncio.gather(
        jobs.run_one(factory, now=started + timedelta(days=1)),
        _later(0.05, lambda: _pause_from(factory, body_id, constants)),
    )
    assert done is not None and not stopped
    plot, job = await _judged(factory, plot_id)
    assert job.state is JobState.DONE and plot.state is PlotState.PLOWED


async def test_race_the_pause_first_leaves_the_worker_nothing(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The order the lock is built for: the pause takes the job **before** the
    strip, so a worker arriving meanwhile skips the locked row (`SKIP LOCKED`)
    and finds nothing to finish; the strip is paused, the job cancelled."""
    started = datetime.now(UTC)
    plot_id, body_id = await _plough_under_way(factory, constants, started)
    _slow(monkeypatch, plot_, "_running_plough")

    stopped, done = await asyncio.gather(
        _pause_from(factory, body_id, constants),
        _later(0.05, lambda: jobs.run_one(factory, now=started + timedelta(days=1))),
    )
    assert stopped and done is None, "the worker took a job the pause had taken"
    plot, job = await _judged(factory, plot_id)
    assert job.state is JobState.CANCELLED and farm.plow_paused(plot)
