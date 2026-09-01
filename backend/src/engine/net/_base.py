# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The Net's floor: its refusals, where a body stands, the cleaning of what
is written, and the one delivery door letters and posts both go through.
Asks nobody above itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import events
from src.engine.errors import Refusal
from src.engine.jobs import enqueue
from src.models.identity import Body, BodyState
from src.models.job import JobKind


class NetError(Refusal):
    pass


class NoBody(NetError):
    """Writing needs a body: the processor speaks from a head (D-222)."""


class NotAllowed(NetError):
    """Not this channel's author."""


async def _where(session: AsyncSession, identity_id: uuid.UUID) -> uuid.UUID | None:
    """The node of the identity's living body, if it has one."""
    return await session.scalar(
        select(Body.node_id).where(Body.identity_id == identity_id, Body.state == BodyState.ALIVE)
    )


async def _stand(session: AsyncSession, identity_id: uuid.UUID) -> uuid.UUID:
    node_id = await _where(session, identity_id)
    if node_id is None:
        raise NoBody(key="net-no-body")
    return node_id


def _clean(text: str, limit: int, *, what: str) -> str:
    """Trim and bound a piece of writing. `what` names the piece for the refusal
    -- a message variant (`letter`, `name`, `post`), not a word: the word is the
    locale's business (D-251)."""
    cleaned = text.strip()
    if not cleaned:
        raise NetError(key="net-empty", what=what)
    if len(cleaned) > limit:
        raise NetError(key="net-too-long", what=what, limit=limit)
    return cleaned


async def _deliver(
    session: AsyncSession,
    reader: uuid.UUID,
    arrives: datetime,
    *,
    now: datetime,
    event: str,
) -> None:
    """Tell the reader when it reaches them (D-226): at once if the road is
    nothing, otherwise by a job at the moment of arrival. The journal keeps
    no letters (D-222), so this is an announcement, not an event."""
    if arrives <= now:
        await events.announce(session, touches=("net",), identity_id=reader, event=event)
        return
    await enqueue(
        session,
        JobKind.NET_DELIVER,
        arrives,
        payload={"identity": str(reader), "event": event},
    )
