# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""city: change of power (D-162).

Split out of `engine/city.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import current
from src.engine import events
from src.engine import vote as ballots
from src.engine.city._base import FOUNDER_POWERS, FOUNDER_TITLE
from src.engine.city.lookup import by_id
from src.engine.city.office import _office
from src.engine.jobs import enqueue, handler
from src.models.city import (
    City,
    Office,
)
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.job import Job, JobKind


async def ruler(session: AsyncSession, city: City) -> Office | None:
    """The current ruler: the office with the widest set of rights.

    The engine knows rights, not posts (D-154): the "ruler" is whoever has the
    most authority, and what they are called is the city's decision. On a tie
    -- whoever was appointed earlier: seniority settles the dispute without
    inventions.
    """
    offices = (
        (
            await session.execute(
                select(Office).where(Office.city_id == city.id, Office.revoked_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    if not offices:
        return None
    return sorted(offices, key=lambda office: (-len(office.powers or []), office.created_at))[0]


async def hand_over(session: AsyncSession, city: City, who: Identity) -> Office:
    """Hand authority to the elected (D-162).

    The new ruler receives the previous one's set, not an abstract "authority":
    the engine knows rights, not posts. The previous office is vacated -- not
    deleted: who controlled what last month is a matter for the court.
    """
    previous = await ruler(session, city)
    rights = tuple(previous.powers or ()) if previous is not None else FOUNDER_POWERS
    title = previous.title if previous is not None else FOUNDER_TITLE

    if previous is not None:
        if previous.identity_id == who.id:
            return previous
        previous.revoked_at = datetime.now(UTC)
        await session.flush()
        await events.record(
            session,
            EventKind.CITY_OFFICE_REVOKED,
            node_id=city.node_id,
            city_id=str(city.id),
            title=previous.title,
            why="election",
        )

    office = await _office(session, city, who.id, title=title, powers=rights, by=None)
    await events.record(
        session,
        EventKind.CITY_OFFICE_APPOINTED,
        actor_identity_id=who.id,
        node_id=city.node_id,
        city_id=str(city.id),
        whom=who.name,
        whom_identity_id=str(who.id),
        title=title,
        powers=list(rights),
        elected=True,
    )
    await schedule_term(session, city, office)
    return office


async def schedule_term(
    session: AsyncSession, city: City, office: Office, *, now: datetime | None = None
) -> None:
    """Set the term of office, if the charter set one (D-163).

    `ruler_term: fixed` in days: on the term the office is vacated by itself.
    Otherwise "elected for thirty days" means "until they remember themselves".
    """

    if ballots.answer(city, ballots.TERM, "unlimited") != ballots.FIXED_TERM:
        return
    days = ballots.param(city, ballots.TERM)
    if days <= 0:
        return
    end = (now or datetime.now(UTC)) + timedelta(days=days)
    await enqueue(
        session,
        JobKind.RULER_TERM,
        end,
        payload={"city": str(city.id), "office": str(office.id)},
        dedup_key=f"city.term:{office.id}",
    )


@handler(JobKind.RULER_TERM)
async def term_ended(session: AsyncSession, job: Job) -> None:
    """The term is up: the office is vacated, and the city goes to an election if it can."""

    office = await session.get(Office, uuid.UUID(job.payload["office"]))
    city = await by_id(session, uuid.UUID(job.payload["city"]))
    if office is None or city is None or office.revoked_at is not None:
        #: The office was vacated before the term -- by recall or election. A
        #: job retry after a failure does not become a second resignation.
        return

    office.revoked_at = job.run_at
    await session.flush()
    await events.record(
        session,
        EventKind.CITY_OFFICE_REVOKED,
        node_id=city.node_id,
        city_id=str(city.id),
        title=office.title,
        whom_identity_id=str(office.identity_id),
        why="term_expired",
    )
    if ballots.elects_ruler(city):
        await ballots.open_election(session, current(), city, None)


async def dismiss(session: AsyncSession, city: City) -> Office | None:
    """Remove the ruler: the recall passed. The city stays without authority until the election."""
    previous = await ruler(session, city)
    if previous is None:
        return None
    previous.revoked_at = datetime.now(UTC)
    await session.flush()
    await events.record(
        session,
        EventKind.CITY_OFFICE_REVOKED,
        node_id=city.node_id,
        city_id=str(city.id),
        title=previous.title,
        whom_identity_id=str(previous.identity_id),
        why="recalled",
    )
    return previous
