# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Correspondence: a thread between two people, letters that travel at the
road's speed, and what is unread on arrival.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.engine import events
from src.engine.jobs import handler
from src.engine.net._base import NetError, _clean, _deliver, _stand, _where
from src.engine.net.road import delay_between
from src.models.identity import Identity
from src.models.job import Job, JobKind
from src.models.net import (
    NetMessage,
    NetParty,
    NetThread,
)
from src.runtime import (
    NET_PAGE,
    NET_SEARCH_LIMIT,
    NET_TEXT_LIMIT,
)

# --- correspondence ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ThreadView:
    id: str
    #: The other party.
    who: str
    surname: str
    last_at: datetime | None
    #: The last letter the reader can already see.
    preview: str | None
    unread: int


@dataclass(frozen=True, slots=True)
class Letter:
    id: str
    who: str
    mine: bool
    text: str
    sent_at: datetime
    delivered_at: datetime


def _pair_key(one: uuid.UUID, other: uuid.UUID) -> str:
    return ":".join(sorted((str(one), str(other))))


async def open_thread(session: AsyncSession, me: Identity, other: Identity) -> NetThread:
    """The correspondence with somebody: found, or started empty. Deciding to
    write is already a thread (D-222)."""
    if me.id == other.id:
        raise NetError(key="net-letter-to-self")
    key = _pair_key(me.id, other.id)
    thread = (
        await session.execute(select(NetThread).where(NetThread.pair_key == key))
    ).scalar_one_or_none()
    if thread is None:
        thread = NetThread(pair_key=key)
        session.add(thread)
        await session.flush()
        session.add_all(
            [
                NetParty(thread_id=thread.id, identity_id=me.id),
                NetParty(thread_id=thread.id, identity_id=other.id),
            ]
        )
        await session.flush()
    return thread


async def _party(session: AsyncSession, thread_id: uuid.UUID, identity_id: uuid.UUID) -> NetParty:
    party = (
        await session.execute(
            select(NetParty).where(
                NetParty.thread_id == thread_id, NetParty.identity_id == identity_id
            )
        )
    ).scalar_one_or_none()
    if party is None:
        raise NetError(key="net-not-your-thread")
    return party


async def write(
    session: AsyncSession,
    constants: Constants,
    me: Identity,
    thread_id: uuid.UUID,
    text: str,
    *,
    now: datetime | None = None,
) -> NetMessage:
    """Send a letter. It leaves at once and arrives by the road (D-222)."""
    moment = now or datetime.now(UTC)
    cleaned = _clean(text, NET_TEXT_LIMIT, what="letter")
    await _party(session, thread_id, me.id)
    here = await _stand(session, me.id)

    #: The reader's road: the other party's body. Several readers would each
    #: need a delay of their own; with two there is one.
    others = (
        (
            await session.execute(
                select(NetParty.identity_id).where(
                    NetParty.thread_id == thread_id, NetParty.identity_id != me.id
                )
            )
        )
        .scalars()
        .all()
    )
    delay = timedelta(0)
    for reader in others:
        there = await _where(session, reader)
        delay = max(delay, await delay_between(session, constants, here, there, now=moment))

    letter = NetMessage(
        thread_id=thread_id,
        identity_id=me.id,
        text=cleaned,
        sent_at=moment,
        delivered_at=moment + delay,
    )
    session.add(letter)
    thread = await session.get(NetThread, thread_id)
    thread.last_at = moment
    await session.flush()
    for reader in others:
        await _deliver(session, reader, letter.delivered_at, now=moment, event="net.letter")
    return letter


@handler(JobKind.NET_DELIVER)
async def delivered(session: AsyncSession, job: Job) -> None:
    """The road is walked: the reader hears that something has reached them."""
    await events.announce(
        session,
        touches=("net",),
        identity_id=uuid.UUID(job.payload["identity"]),
        event=str(job.payload.get("event") or "net.letter"),
    )


def _visible(identity_id: uuid.UUID, now: datetime):
    """A letter the reader can see: their own at once, others' when delivered."""
    return or_(NetMessage.identity_id == identity_id, NetMessage.delivered_at <= now)


async def threads(
    session: AsyncSession, me_id: uuid.UUID, *, now: datetime | None = None
) -> list[ThreadView]:
    """The reader's correspondence, latest first, with what is waiting in each."""
    moment = now or datetime.now(UTC)
    mine = select(NetParty.thread_id).where(NetParty.identity_id == me_id).subquery()
    rows = (
        await session.execute(
            select(NetThread, NetParty, Identity)
            .join(NetParty, NetParty.thread_id == NetThread.id)
            .join(Identity, Identity.id == NetParty.identity_id)
            .where(NetThread.id.in_(select(mine)), NetParty.identity_id != me_id)
            .order_by(NetThread.last_at.desc().nulls_last(), NetThread.created_at.desc())
        )
    ).all()
    out: list[ThreadView] = []
    for thread, _, other in rows:
        me = await _party(session, thread.id, me_id)
        last = (
            await session.execute(
                select(NetMessage)
                .where(NetMessage.thread_id == thread.id, _visible(me_id, moment))
                .order_by(NetMessage.delivered_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        out.append(
            ThreadView(
                id=str(thread.id),
                who=other.name,
                surname=other.surname,
                last_at=thread.last_at,
                preview=None if last is None else last.text,
                unread=await _unread_in(session, thread.id, me_id, me.read_at, moment),
            )
        )
    return out


async def _unread_in(
    session: AsyncSession,
    thread_id: uuid.UUID,
    me_id: uuid.UUID,
    read_at: datetime | None,
    now: datetime,
) -> int:
    stmt = (
        select(func.count())
        .select_from(NetMessage)
        .where(
            NetMessage.thread_id == thread_id,
            NetMessage.identity_id != me_id,
            NetMessage.delivered_at <= now,
        )
    )
    if read_at is not None:
        stmt = stmt.where(NetMessage.delivered_at > read_at)
    return int(await session.scalar(stmt) or 0)


async def read_thread(
    session: AsyncSession,
    me_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> list[Letter]:
    """The thread as the reader sees it now. Reading marks it read."""
    moment = now or datetime.now(UTC)
    party = await _party(session, thread_id, me_id)
    rows = (
        await session.execute(
            select(NetMessage, Identity.name)
            .join(Identity, Identity.id == NetMessage.identity_id)
            .where(NetMessage.thread_id == thread_id, _visible(me_id, moment))
            .order_by(NetMessage.delivered_at.desc())
            .limit(NET_PAGE)
        )
    ).all()
    party.read_at = moment
    await session.flush()
    return [
        Letter(
            id=str(letter.id),
            who=who,
            mine=letter.identity_id == me_id,
            text=letter.text,
            sent_at=letter.sent_at,
            delivered_at=letter.delivered_at,
        )
        for letter, who in reversed(rows)
    ]


async def unread_letters(
    session: AsyncSession, me_id: uuid.UUID, *, now: datetime | None = None
) -> int:
    """Delivered and unread letters across all threads: the tab's count."""
    moment = now or datetime.now(UTC)
    stmt = (
        select(func.count())
        .select_from(NetMessage)
        .join(
            NetParty,
            and_(NetParty.thread_id == NetMessage.thread_id, NetParty.identity_id == me_id),
        )
        .where(
            NetMessage.identity_id != me_id,
            NetMessage.delivered_at <= moment,
            or_(NetParty.read_at.is_(None), NetMessage.delivered_at > NetParty.read_at),
        )
    )
    return int(await session.scalar(stmt) or 0)


async def find_people(
    session: AsyncSession, query: str, *, exclude: uuid.UUID
) -> list[tuple[str, str]]:
    """Names starting with what was typed: whom to write to. Names are public (D-058)."""
    typed = query.strip()
    if not typed:
        return []
    rows = (
        await session.execute(
            select(Identity.name, Identity.surname)
            .where(Identity.name.ilike(f"{typed}%"), Identity.id != exclude)
            .order_by(Identity.name)
            .limit(NET_SEARCH_LIMIT)
        )
    ).all()
    return [(name, surname) for name, surname in rows]
