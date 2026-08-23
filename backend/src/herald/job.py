# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The herald's job: carry news from the event journal to Discord.

Built like the world tick (`engine/tick.py`): the job queues the next itself
and carries the cursor in its payload. There is deliberately no separate
table for "how far was read" -- the feed's state is not the world's state,
and nobody is hurt if the feed's state is wiped.

Three decisions easy to forget in half a year:

* **The first pass does not resend history.** No cursor -- so the feed starts
  here: no point dumping the world's whole journal into the channel on first
  start. For the same reason herald downtime is not resent: this is a feed,
  not a journal, and the journal is in the database and is not going anywhere.
* **Sending happens inside the job's transaction.** Network under an open
  transaction is the price for the cursor and the send being committed
  together. A pass is bounded by `HERALD_BATCH` events and `HERALD_TIMEOUT`
  seconds, so the transaction is short. The flip side is honest: if Discord
  answered but the commit failed, the job retry sends the same lines a second
  time. A duplicate in the feed is cheaper than a gap.
* **Silence on empty config is not a stop.** Without a webhook the job still
  moves the cursor: otherwise a herald switched on a month later would start
  with a month's tail.

A known inaccuracy, recorded on purpose: the cursor goes by `event.id`, and
the event number is taken from the sequence **before** commit. An event that
got its number earlier but committed after the pass ceiling will not make it
into the feed. For a journal that would be a defect, for a feed it is not:
the journal is complete and lies in the database, and the feed proves nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine.jobs import enqueue, handler
from src.herald.chronicle import PUBLIC, compose
from src.herald.webhook import chunks, send
from src.models.event import Event
from src.models.job import Job, JobKind
from src.runtime import HERALD_BATCH, HERALD_PERIOD
from src.settings import settings

log = logging.getLogger(__name__)

Sender = Callable[[str, str], Awaitable[None]]


def _slot(moment: datetime) -> int:
    """The interval number since the epoch -- also the idempotency key."""
    return int(moment.timestamp() // HERALD_PERIOD.total_seconds())


async def _last_event_id(session: AsyncSession) -> int:
    return (await session.execute(select(func.max(Event.id)))).scalar() or 0


async def run_once(
    session: AsyncSession,
    *,
    after: int | None,
    url: str,
    sender: Sender = send,
) -> int:
    """One pass of the feed. Returns the new cursor.

    The cursor moves to the journal's last event, not the last **published**
    one: otherwise every next pass would reread all the silent ones accumulated
    since -- and most of the journal is silent.
    """
    ceiling = await _last_event_id(session)
    if after is None:
        log.info("herald starts the feed from event %s", ceiling)
        return ceiling
    if not url:
        return ceiling

    events_ = (
        (
            await session.execute(
                select(Event)
                .where(Event.id > after, Event.id <= ceiling, Event.kind.in_(PUBLIC))
                .order_by(Event.id)
                .limit(HERALD_BATCH)
            )
        )
        .scalars()
        .all()
    )

    lines = await compose(session, events_)
    for piece in chunks(lines):
        await sender(url, piece)
    if lines:
        log.info("herald delivered %s chronicle lines", len(lines))

    #: The batch hit the limit -- so there is more beyond the ceiling; the
    #: cursor stops at the last taken, and the rest goes with the next pass.
    if len(events_) >= HERALD_BATCH:
        return events_[-1].id
    return ceiling


@handler(JobKind.HERALD_POST)
async def post(session: AsyncSession, job: Job) -> None:
    boundary = await run_once(
        session,
        after=None if job.payload.get("after") is None else int(job.payload["after"]),
        url=settings().discord_webhook,
    )
    #: The next link -- not earlier than now. The world clock catches up on
    #: what was missed tick by tick, but the feed has nothing to catch up on:
    #: after a day of downtime it would otherwise spin seven hundred empty
    #: links to reach the same point.
    next_ = max(job.run_at + HERALD_PERIOD, datetime.now(UTC))
    await _schedule(session, next_, after=boundary)


async def _schedule(session: AsyncSession, run_at: datetime, *, after: int) -> None:
    await enqueue(
        session,
        JobKind.HERALD_POST,
        run_at,
        payload={"after": after},
        dedup_key=f"herald.post:{_slot(run_at)}",
    )


async def ensure_scheduled(session: AsyncSession, now: datetime | None = None) -> None:
    """Start the herald at worker startup.

    While the chain is alive, the current interval's key is already taken by
    its link, and no second herald appears here. If the chain ever broke for
    good (the job gave up after all attempts), this call starts it anew --
    from a clean slate, without a tail for the downtime.
    """

    moment = now or datetime.now(UTC)
    await enqueue(
        session,
        JobKind.HERALD_POST,
        moment,
        dedup_key=f"herald.post:{_slot(moment)}",
    )
