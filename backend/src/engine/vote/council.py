# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The council as a body (D-164): its mode and seats, who sits and vacates,
who proposes a law, and which circle votes on what.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import city as town
from src.engine import events
from src.engine.vote._base import (
    APPOINTED_COUNCIL,
    APPROVAL,
    BY_COUNCIL,
    CITIZENS,
    COUNCIL,
    COUNCIL_VOTERS,
    ELECTED_BY_COUNCIL,
    LAWMAKER,
    NO_COUNCIL,
    RECALL_BY_COUNCIL,
    RECALL_RULE,
    SELECTION,
    NoCouncil,
    NoVoice,
    answer,
    may_vote,
    param,
)
from src.models.city import City, CouncilSeat, Power
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.vote import VoteKind


def council_mode(city: City) -> str:
    return answer(city, COUNCIL, NO_COUNCIL)


def council_seats(city: City) -> int:
    """How many seats the charter set. Zero seats equals no council."""
    return int(param(city, COUNCIL))


def has_council(city: City) -> bool:
    return council_mode(city) != NO_COUNCIL and council_seats(city) > 0


async def council_of(session: AsyncSession, city: City) -> list[CouncilSeat]:
    """Occupied council seats."""
    return list(
        (
            await session.execute(
                select(CouncilSeat).where(
                    CouncilSeat.city_id == city.id,
                    CouncilSeat.vacated_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )


async def in_council(session: AsyncSession, city: City, identity_id: uuid.UUID) -> bool:
    return any(place.identity_id == identity_id for place in await council_of(session, city))


def voters_for(city: City, kind: VoteKind) -> str:
    """Who votes on this subject (D-164, D-165).

    The circle is determined by the subject **and** the charter: the council
    approves a law if so stated; the ruler is elected and recalled by whoever
    the charter gave it to. Everything else is the citizens' business.

    An empty chamber locks neither laws nor authority: a city with zero seats
    decides itself, as a whole city, and a law is applied by whoever proposed
    it. A charter that cannot be executed literally is executed by meaning
    rather than blocking the city forever.
    """
    by_council = {
        VoteKind.LAW: answer(city, APPROVAL, "ruler") == BY_COUNCIL,
        VoteKind.ELECTION: answer(city, SELECTION, "founder") == ELECTED_BY_COUNCIL,
        VoteKind.RECALL: answer(city, RECALL_RULE, "never") == RECALL_BY_COUNCIL,
    }.get(kind, False)
    if by_council and has_council(city):
        return COUNCIL_VOTERS
    return CITIZENS


async def may_propose(session: AsyncSession, city: City, identity_id: uuid.UUID) -> bool:
    """Whether this person may propose laws (`lawmaker`).

    The `laws` right always proposes a law -- that is authority. The council is
    added to it when the charter answers "the council proposes": then there are
    as many legislators as seats, and the ruler is not the only one among them.
    """
    if answer(city, LAWMAKER, "ruler") != BY_COUNCIL:
        return False
    return await in_council(session, city, identity_id)


async def seat(session: AsyncSession, city: City, who: Identity, *, how: str) -> CouncilSeat:
    """Seat a person on the council. No more seats than the charter set."""
    if not has_council(city):
        raise NoCouncil(key="vote-no-council")
    occupied_ = await council_of(session, city)
    if any(place.identity_id == who.id for place in occupied_):
        return next(m for m in occupied_ if m.identity_id == who.id)
    if len(occupied_) >= council_seats(city):
        raise NoCouncil(key="vote-council-full", seats=council_seats(city))

    place = CouncilSeat(city_id=city.id, identity_id=who.id, how=how)
    session.add(place)
    await session.flush()
    await events.record(
        session,
        EventKind.COUNCIL_SEATED,
        actor_identity_id=who.id,
        node_id=city.node_id,
        city_id=str(city.id),
        who=who.name,
        how=how,
    )
    return place


async def vacate(session: AsyncSession, city: City, who: Identity) -> bool:
    """Vacate a seat. The record stays: who voted is a matter for the court."""
    for place in await council_of(session, city):
        if place.identity_id != who.id:
            continue
        place.vacated_at = datetime.now(UTC)
        await session.flush()
        await events.record(
            session,
            EventKind.COUNCIL_VACATED,
            node_id=city.node_id,
            city_id=str(city.id),
            who=who.name,
        )
        return True
    return False


async def appoint_to_council(
    session: AsyncSession, city: City, by: Identity, who: Identity
) -> CouncilSeat:
    """Appoint to the council. Only where the charter gave the seats to the ruler."""

    if council_mode(city) != APPOINTED_COUNCIL:
        raise NoCouncil(key="vote-council-not-appointed")
    await town.require(session, by.id, city, Power.OFFICES)
    if not await may_vote(session, city, who.id):
        raise NoVoice(key="vote-council-needs-voice")
    return await seat(session, city, who, how=APPOINTED_COUNCIL)
