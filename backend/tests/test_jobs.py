"""Журнал заданий: ровно один раз, даже если процесс перезапустили.

На этом держатся партии, караваны, рост урожая и суточные списания
(01-tech-notes, паттерн 1). Ошибка здесь не ловится игрой — она проявляется
двойным списанием содержания через неделю после запуска.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src import herald  # noqa: F401 — регистрирует обработчик хроники
from src.engine import jobs, tick
from src.models.event import Event
from src.models.job import Job, JobKind, JobState

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


async def test_повторная_постановка_не_плодит_задание(session: AsyncSession) -> None:
    first = await jobs.enqueue(session, JobKind.WORLD_TICK, NOW, dedup_key="tick:1")
    second = await jobs.enqueue(session, JobKind.WORLD_TICK, NOW, dedup_key="tick:1")
    await session.commit()

    assert first is not None
    assert second is None, "ключ идемпотентности обязан отсечь дубль"
    total = await session.scalar(select(func.count()).select_from(Job))
    assert total == 1


async def test_тик_выполняется_и_ставит_следующий(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session, session.begin():
        await tick.ensure_scheduled(session, NOW)

    done = await jobs.run_one(factory, now=NOW)
    assert done is not None
    assert done.state is JobState.DONE

    async with factory() as session:
        pending = (
            await session.execute(
                select(Job).where(Job.state == JobState.PENDING, Job.kind == JobKind.WORLD_TICK)
            )
        ).scalars().all()
        assert len(pending) == 1, "часы мира обязаны продолжать идти"
        assert pending[0].run_at > NOW


async def test_будущее_задание_не_берётся(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session, session.begin():
        await jobs.enqueue(session, JobKind.WORLD_TICK, NOW + timedelta(hours=1))

    assert await jobs.run_one(factory, now=NOW) is None


async def test_эффект_и_отметка_фиксируются_вместе(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Событие тика и его отметка «выполнено» — одна транзакция."""
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


async def test_упавшее_задание_откатывает_свои_эффекты(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Половина эффекта хуже отсутствия эффекта: откатывается всё."""
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


async def test_задание_сдаётся_после_предела_попыток(
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


async def test_два_воркера_не_берут_одно_задание(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """`FOR UPDATE SKIP LOCKED`: очередь разбирается без координации."""
    async with factory() as session, session.begin():
        await jobs.enqueue(session, JobKind.WORLD_TICK, NOW, dedup_key="один")

    first, second = await asyncio.gather(
        jobs.run_one(factory, now=NOW, worker="A"),
        jobs.run_one(factory, now=NOW, worker="B"),
    )
    taken = [job for job in (first, second) if job is not None]
    assert len(taken) == 1, "задание обязано достаться ровно одному воркеру"


async def test_все_виды_заданий_имеют_обработчик() -> None:
    """Задание без обработчика — отложенное расхождение мира с самим собой.

    Обработчики регистрируются импортом, и здесь импортируется ровно то же,
    что импортирует воркер: движок и глашатай. Появится третий пакет с
    заданиями — его сюда придётся дописать, и это правильная цена за то, что
    проверка идёт при старте, а не в тике посреди ночи.
    """
    jobs.require_handlers()


async def test_журнал_событий_неизменяем(session: AsyncSession) -> None:
    from sqlalchemy.exc import DBAPIError

    from src.engine import events

    await events.record(session, "тест.событие")
    await session.commit()

    with pytest.raises(DBAPIError, match="только для добавления"):
        await session.execute(Event.__table__.delete())
        await session.commit()
    await session.rollback()
