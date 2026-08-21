# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Execution of the job journal.

Idempotency rests on three things, and all three are mandatory:

1. **One job -- one transaction.** The handler and the "done" mark are
   committed together. The process crashed mid-job -- everything rolled back,
   the job stayed pending and will run again. Exactly once, not "roughly".
2. **`FOR UPDATE SKIP LOCKED`.** Several workers drain the queue without
   jostling or duplicating.
3. **`dedup_key`.** Queueing the same job again does not create a second one.

From the first point follows a constraint easy to forget: **the job's effect
must be in the database**. Everything going outside (an email, a push, a call
to somebody's API) is queued as a separate job and retried on failure.
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
    """Queue a job. Returns None if such a job is already queued."""
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
    """Take one job, locking the row until the end of the transaction."""
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
    """Run one ready job. Returns it, or None if the queue is empty."""
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
            log.error("job %s: %s", job_id, job.last_error)
            await session.commit()
            return job

        try:
            await action(session, job)
        except Exception as exc:  # noqa: BLE001 -- the job's fate is decided below
            #: The rollback takes both the job's effects and the attempt mark --
            #: so the attempt is recorded in a separate transaction.
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
    """Drain the queue up to `limit` jobs. Returns the number executed."""
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
    """Mark a failure in a separate transaction -- the job's effects have already rolled back."""
    async with factory() as session, session.begin():
        job = await session.get(Job, job_id, with_for_update=True)
        if job is None:  # pragma: no cover
            return
        #: The rollback took the incremented attempt counter too -- we count
        #: again here, otherwise a broken job would spin forever.
        job.attempts += 1
        job.last_error = f"{type(exc).__name__}: {exc}"[:JOB_ERROR_LIMIT]
        if job.attempts >= JOB_MAX_ATTEMPTS:
            job.state = JobState.FAILED
            job.finished_at = moment
            log.error("job %s gave up after %s attempts: %s", job.id, job.attempts, exc)
        else:
            job.state = JobState.PENDING
            job.run_at = moment + JOB_RETRY_BASE * (JOB_RETRY_GROWTH ** (job.attempts - 1))
            job.locked_by = None
            job.locked_at = None
            log.warning("job %s will retry at %s: %s", job.id, job.run_at, exc)


async def _reload(factory: async_sessionmaker[AsyncSession], job_id: Any) -> Job | None:
    async with factory() as session:
        return await session.get(Job, job_id)


def require_handlers() -> None:
    """Check that every job kind has a handler.

    Called at startup: a job without a handler is a deferred silent divergence
    of the world from itself.
    """

    missing = {kind.value for kind in JobKind} - registered_kinds()
    if missing:
        raise UnknownJobKind(
            "нет обработчиков для видов заданий: " + ", ".join(sorted(missing))
        )
