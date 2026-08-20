"""The job journal: exactly once, even if the process was restarted.

Batches, caravans, harvest growth and daily write-offs rest on this
(01-tech-notes, pattern 1). An error here is not caught by the game -- it
shows as a double maintenance write-off a week after launch.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src import herald  # noqa: F401 -- registers the chronicle handler
from src.engine import jobs, tick
from src.models.event import Event
from src.models.job import Job, JobKind, JobState

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


async def test_repeated_queueing_does_not_duplicate_job(session: AsyncSession) -> None:
    first = await jobs.enqueue(session, JobKind.WORLD_TICK, NOW, dedup_key="tick:1")
    second = await jobs.enqueue(session, JobKind.WORLD_TICK, NOW, dedup_key="tick:1")
    await session.commit()

    assert first is not None
    assert second is None, "ключ идемпотентности обязан отсечь дубль"
    total = await session.scalar(select(func.count()).select_from(Job))
    assert total == 1


async def test_tick_runs_and_queues_next(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session, session.begin():
        await tick.ensure_scheduled(session, NOW)

    #: `ensure_scheduled` lays down the world tick and the daily one for the
    #: same moment, and which of the two the queue hands over first is nobody's
    #: business: `_claim` orders by firing time, and the times are equal. So the
    #: queue is drained, not sipped -- otherwise the test checks the order of
    #: the Postgres plan rather than the world clock.
    ran = [await jobs.run_one(factory, now=NOW) for _ in range(2)]
    assert all(job is not None and job.state is JobState.DONE for job in ran)
    assert {job.kind for job in ran} == {JobKind.WORLD_TICK, JobKind.DAILY_TICK}

    async with factory() as session:
        pending = (
            await session.execute(
                select(Job).where(Job.state == JobState.PENDING, Job.kind == JobKind.WORLD_TICK)
            )
        ).scalars().all()
        assert len(pending) == 1, "часы мира обязаны продолжать идти"
        assert pending[0].run_at > NOW


async def test_future_job_not_taken(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session, session.begin():
        await jobs.enqueue(session, JobKind.WORLD_TICK, NOW + timedelta(hours=1))

    assert await jobs.run_one(factory, now=NOW) is None


async def test_effect_and_mark_committed_together(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """The tick event and its "done" mark are one transaction."""
    async with factory() as session, session.begin():
        await tick.ensure_scheduled(session, NOW)

    await jobs.run_due(factory, limit=2, now=NOW)

    async with factory() as session:
        ticks = await session.scalar(
            select(func.count()).select_from(Event).where(Event.kind == "tick.ran")
        )
        finished = await session.scalar(
            select(func.count()).select_from(Job).where(Job.state == JobState.DONE)
        )
        assert ticks == finished == 2


async def test_failed_job_rolls_back_its_effects(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Half an effect is worse than no effect: everything rolls back."""
    from src.engine import events

    async def broken(session: AsyncSession, job: Job) -> None:
        await events.record(session, "тест.половина_дела")
        raise RuntimeError("оборвалось на середине")

    jobs._HANDLERS["тест.сломанное"] = broken
    try:
        async with factory() as session, session.begin():
            await jobs.enqueue(session, JobKind.WORLD_TICK, NOW)
            job = (await session.execute(select(Job))).scalar_one()
            job.kind = "тест.сломанное"

        result = await jobs.run_one(factory, now=NOW)
        assert result is not None
        assert result.state is JobState.PENDING, "задание обязано вернуться в очередь"
        assert result.attempts == 1
        assert result.run_at > NOW, "повтор откладывается, а не крутится вплотную"

        async with factory() as session:
            leaked = await session.scalar(
                select(func.count()).select_from(Event).where(Event.kind == "тест.половина_дела")
            )
            assert leaked == 0, "эффекты упавшего задания не должны остаться в мире"
    finally:
        jobs._HANDLERS.pop("тест.сломанное", None)


async def test_job_gives_up_after_attempt_limit(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    from src.runtime import JOB_MAX_ATTEMPTS

    async def broken(session: AsyncSession, job: Job) -> None:
        raise RuntimeError("всегда падает")

    jobs._HANDLERS["тест.безнадёжное"] = broken
    try:
        async with factory() as session, session.begin():
            await jobs.enqueue(session, JobKind.WORLD_TICK, NOW)
            job = (await session.execute(select(Job))).scalar_one()
            job.kind = "тест.безнадёжное"

        moment = NOW
        for _ in range(JOB_MAX_ATTEMPTS):
            result = await jobs.run_one(factory, now=moment)
            assert result is not None
            moment = max(moment, result.run_at)

        assert result.state is JobState.FAILED
        assert "всегда падает" in (result.last_error or "")
    finally:
        jobs._HANDLERS.pop("тест.безнадёжное", None)


async def test_two_workers_do_not_take_same_job(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """`FOR UPDATE SKIP LOCKED`: the queue is drained without coordination."""
    async with factory() as session, session.begin():
        await jobs.enqueue(session, JobKind.WORLD_TICK, NOW, dedup_key="один")

    first, second = await asyncio.gather(
        jobs.run_one(factory, now=NOW, worker="A"),
        jobs.run_one(factory, now=NOW, worker="B"),
    )
    taken = [job for job in (first, second) if job is not None]
    assert len(taken) == 1, "задание обязано достаться ровно одному воркеру"


async def test_all_job_kinds_have_handler() -> None:
    """A job without a handler is a deferred divergence of the world from itself.

    Handlers are registered by import, and exactly the same is imported here
    as the worker imports: the engine and the herald. A third package with
    jobs appears -- it will have to be added here, and that is the right price
    for the check running at startup rather than in a tick in the middle of the night.
    """

    jobs.require_handlers()


async def test_event_journal_immutable(session: AsyncSession) -> None:
    from sqlalchemy.exc import DBAPIError

    from src.engine import events

    await events.record(session, "тест.событие")
    await session.commit()

    with pytest.raises(DBAPIError, match="только для добавления"):
        await session.execute(Event.__table__.delete())
        await session.commit()
    await session.rollback()
