"""Исполнение журнала заданий.

Идемпотентность держится на трёх вещах, и все три обязательны:

1. **Одно задание — одна транзакция.** Обработчик и отметка «выполнено»
   фиксируются вместе. Упал процесс посреди задания — откатилось всё, задание
   осталось ожидающим и выполнится ещё раз. Ровно один раз, а не «примерно».
2. **`FOR UPDATE SKIP LOCKED`.** Несколько воркеров разбирают очередь, не
   толкаясь и не дублируя.
3. **`dedup_key`.** Повторная постановка того же задания не создаёт второго.

Из первого пункта следует ограничение, о котором легко забыть: **эффект
задания обязан быть в базе**. Всё, что уходит наружу (письмо, пуш, вызов
чужого API), ставится отдельным заданием и повторяется при сбое.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.job import Job, JobKind, JobState
from src.runtime import (
    JOB_ERROR_LIMIT,
    JOB_MAX_ATTEMPTS,
    JOB_RETRY_BASE,
    JOB_RETRY_GROWTH,
)

log = logging.getLogger(__name__)

Handler = Callable[[AsyncSession, Job], Awaitable[None]]

_HANDLERS: dict[str, Handler] = {}


class UnknownJobKind(Exception):
    pass


def handler(kind: JobKind) -> Callable[[Handler], Handler]:
    def register(func: Handler) -> Handler:
        if kind.value in _HANDLERS:
            raise RuntimeError(f"обработчик {kind} уже зарегистрирован")
        _HANDLERS[kind.value] = func
        return func

    return register


def registered_kinds() -> frozenset[str]:
    return frozenset(_HANDLERS)


async def enqueue(
    session: AsyncSession,
    kind: JobKind,
    run_at: datetime,
    *,
    payload: dict[str, Any] | None = None,
    dedup_key: str | None = None,
    cause_event_id: int | None = None,
    body_id: Any = None,
) -> Job | None:
    """Поставить задание. Возвращает None, если такое уже стоит."""
    values = {
        "kind": kind.value,
        "state": JobState.PENDING.value,
        "run_at": run_at,
        "payload": payload or {},
        "dedup_key": dedup_key,
        "cause_event_id": cause_event_id,
        "body_id": body_id,
    }
    stmt = insert(Job).values(**values).returning(Job.id)
    if dedup_key is not None:
        stmt = stmt.on_conflict_do_nothing(index_elements=[Job.dedup_key])
    job_id = (await session.execute(stmt)).scalar_one_or_none()
    if job_id is None:
        return None
    await session.flush()
    return await session.get(Job, job_id)


async def _claim(session: AsyncSession, now: datetime, worker: str) -> Job | None:
    """Взять одно задание, заблокировав строку до конца транзакции."""
    stmt = (
        select(Job)
        .where(Job.state == JobState.PENDING, Job.run_at <= now)
        .order_by(Job.run_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        return None
    job.locked_by = worker
    job.locked_at = now
    job.attempts += 1
    return job


async def run_one(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    worker: str = "worker",
) -> Job | None:
    """Выполнить одно готовое задание. Возвращает его либо None, если очередь пуста."""
    moment = now or datetime.now(UTC)

    async with factory() as session:
        job = await _claim(session, moment, worker)
        if job is None:
            await session.rollback()
            return None

        job_id = job.id
        action = _HANDLERS.get(job.kind)
        if action is None:
            job.state = JobState.FAILED
            job.last_error = f"нет обработчика для {job.kind}"
            job.finished_at = moment
            log.error("задание %s: %s", job_id, job.last_error)
            await session.commit()
            return job

        try:
            await action(session, job)
        except Exception as exc:  # noqa: BLE001 — решение о судьбе задания ниже
            #: Откат уносит и эффекты задания, и отметку о попытке — поэтому
            #: попытка записывается отдельной транзакцией.
            await session.rollback()
            await _mark_failure(factory, job_id, exc, moment)
            return await _reload(factory, job_id)

        job.state = JobState.DONE
        job.finished_at = moment
        job.last_error = None
        await session.commit()
        return job


async def run_due(
    factory: async_sessionmaker[AsyncSession],
    *,
    limit: int,
    now: datetime | None = None,
    worker: str = "worker",
) -> int:
    """Разобрать очередь до `limit` заданий. Возвращает число выполненных."""
    done = 0
    for _ in range(limit):
        job = await run_one(factory, now=now, worker=worker)
        if job is None:
            break
        done += 1
    return done


async def _mark_failure(
    factory: async_sessionmaker[AsyncSession],
    job_id: Any,
    exc: Exception,
    moment: datetime,
) -> None:
    """Отметить сбой отдельной транзакцией — эффекты задания уже откатились."""
    async with factory() as session, session.begin():
        job = await session.get(Job, job_id, with_for_update=True)
        if job is None:  # pragma: no cover
            return
        #: Откат унёс и увеличенный счётчик попыток — считаем заново здесь,
        #: иначе сломанное задание крутилось бы вечно.
        job.attempts += 1
        job.last_error = f"{type(exc).__name__}: {exc}"[:JOB_ERROR_LIMIT]
        if job.attempts >= JOB_MAX_ATTEMPTS:
            job.state = JobState.FAILED
            job.finished_at = moment
            log.error("задание %s сдалось после %s попыток: %s", job.id, job.attempts, exc)
        else:
            job.state = JobState.PENDING
            job.run_at = moment + JOB_RETRY_BASE * (JOB_RETRY_GROWTH ** (job.attempts - 1))
            job.locked_by = None
            job.locked_at = None
            log.warning("задание %s повторится в %s: %s", job.id, job.run_at, exc)


async def _reload(factory: async_sessionmaker[AsyncSession], job_id: Any) -> Job | None:
    async with factory() as session:
        return await session.get(Job, job_id)


def require_handlers() -> None:
    """Проверить, что у каждого вида задания есть обработчик.

    Вызывается при старте: задание без обработчика — это отложенное молчаливое
    расхождение мира с самим собой.
    """
    missing = {kind.value for kind in JobKind} - registered_kinds()
    if missing:
        raise UnknownJobKind(
            "нет обработчиков для видов заданий: " + ", ".join(sorted(missing))
        )
