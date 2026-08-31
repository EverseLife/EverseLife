# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Citizenship: who belongs to a city, and how they come and go (D-160).

One citizenship per person, and the city decides who gets it -- by an open
door, by an application, or by invitation, according to its own charter. The
engine holds the rule that there is exactly one, and refuses everything that
would make a second.

Three functions live here that used to sit elsewhere, and the moves are the
whole reason this file is a file. `_enrol_founder` was among the offices,
because a founder is given a post at the same moment; `bind` was among the
laws, because it reads the charter's print conditions; `describe` was there
too, though it reads no law at all -- it writes the city's own text and is
guarded by the right to admit people. Leaving the first two where they were is
what made the city's four sections a cycle instead of a stack: offices reached
down into citizenship, laws reached down into citizenship, and citizenship
reached back up into both.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, travel
from src.engine.city._base import (
    CityError,
    NotAllowed,
)
from src.engine.city.hall import require_at_hall
from src.engine.city.law import spawn_terms
from src.engine.city.lookup import by_id
from src.engine.city.office import require
from src.engine.jobs import enqueue, handler
from src.models.city import (
    Citizen,
    CitizenshipRequest,
    City,
    Power,
)
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.job import Job, JobKind
from src.runtime import CITY_ABOUT_LIMIT

#: The charter question "how are citizens admitted" and its options (`laws.json`).
ADMISSION = "citizenship_admission"


OPEN, APPLICATION, INVITE = "open", "application", "invite"


class NotCitizen(CityError):
    """This is for citizens. Who exactly is decided by the city, not the engine."""


class AlreadyCitizen(CityError):
    """One citizenship per person: leave the previous city first."""


class Bound(CityError):
    """The term of the obligation taken at printing has not expired yet (D-184)."""


def admission(city: City) -> str:
    """How this city admits citizens: the charter's answer, or "open"."""
    return str((city.charter or {}).get(ADMISSION) or OPEN)


async def citizenship(session: AsyncSession, identity_id: uuid.UUID) -> Citizen | None:
    """The identity's citizenship, if any. There is one -- that is how the record works."""
    return (
        await session.execute(select(Citizen).where(Citizen.identity_id == identity_id))
    ).scalar_one_or_none()


async def is_citizen(session: AsyncSession, identity_id: uuid.UUID, city: City) -> bool:
    entry = await citizenship(session, identity_id)
    return entry is not None and entry.city_id == city.id


async def citizens_of(session: AsyncSession, city: City) -> list[Citizen]:
    return list(
        (await session.execute(select(Citizen).where(Citizen.city_id == city.id))).scalars().all()
    )


async def request_of(
    session: AsyncSession, identity_id: uuid.UUID, city: City
) -> CitizenshipRequest | None:
    return (
        await session.execute(
            select(CitizenshipRequest).where(
                CitizenshipRequest.identity_id == identity_id,
                CitizenshipRequest.city_id == city.id,
            )
        )
    ).scalar_one_or_none()


async def requests_of(session: AsyncSession, city: City) -> list[CitizenshipRequest]:
    """The queue: who applies and who was invited. Reference, not a decision."""
    return list(
        (
            await session.execute(
                select(CitizenshipRequest).where(CitizenshipRequest.city_id == city.id)
            )
        )
        .scalars()
        .all()
    )


async def join(session: AsyncSession, body, city: City) -> Citizen | CitizenshipRequest:
    """Apply for citizenship. What comes of it is decided by the city charter (D-160).

    In person, in the administration: citizens are enrolled where the city
    makes every decision (D-155). Returns either citizenship or an application
    -- per the charter's answer to `citizenship_admission`.
    """

    await travel.require_here(session, body)
    await require_at_hall(session, body, city)

    existing_amount = await citizenship(session, body.identity_id)
    if existing_amount is not None:
        if existing_amount.city_id == city.id:
            raise AlreadyCitizen(key="city-already-citizen-here")
        raise AlreadyCitizen(key="city-citizenship-is-one")

    order_of = admission(city)
    call = await request_of(session, body.identity_id, city)
    #: An invitation beats the order: invited means admitted, however strict
    #: the charter.
    if order_of == OPEN or (call is not None and call.kind == INVITE):
        if call is not None:
            await session.delete(call)
        return await _enroll(session, city, body.identity_id, why=order_of)

    if order_of == INVITE:
        raise NotAllowed(key="city-by-invitation-only")

    #: An application remains: it is filed and waits for the authority's decision.
    if call is not None:
        return call
    order = CitizenshipRequest(identity_id=body.identity_id, city_id=city.id, kind=APPLICATION)
    session.add(order)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_REQUESTED,
        actor_identity_id=body.identity_id,
        node_id=city.node_id,
        city_id=str(city.id),
        kind_of_request=APPLICATION,
    )
    return order


async def invite(
    session: AsyncSession, by: Identity, city: City, who: Identity
) -> CitizenshipRequest:
    """Invite to citizenship. The invitation waits until the person comes and accepts."""
    await require(session, by.id, city, Power.CITIZENS)
    if await is_citizen(session, who.id, city):
        raise AlreadyCitizen(key="city-already-citizen", who=who.name)

    exists = await request_of(session, who.id, city)
    if exists is not None:
        return exists
    call = CitizenshipRequest(
        identity_id=who.id, city_id=city.id, kind=INVITE, by_identity_id=by.id
    )
    session.add(call)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_REQUESTED,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        who=who.name,
        kind_of_request=INVITE,
    )
    return call


async def admit(session: AsyncSession, by: Identity, city: City, who: Identity) -> Citizen:
    """Approve an application. Right `citizens`: the city's personnel is authority too."""
    await require(session, by.id, city, Power.CITIZENS)
    order = await request_of(session, who.id, city)
    if order is None or order.kind != APPLICATION:
        raise CityError(key="city-no-application")
    if await citizenship(session, who.id) is not None:
        raise AlreadyCitizen(key="city-already-in-a-city", who=who.name)
    await session.delete(order)
    return await _enroll(session, city, who.id, why=APPLICATION, by=by.id)


async def leave(
    session: AsyncSession,
    constants: Constants,
    identity: Identity,
    *,
    now: datetime | None = None,
) -> Citizen:
    """Declare leaving. Citizenship lapses after `city.exit_delay` (D-160).

    Remote: the declaration goes over the Net. The delay exists so that one
    cannot leave the city right before a verdict.
    """

    moment = now or datetime.now(UTC)
    entry = await citizenship(session, identity.id)
    if entry is None:
        raise NotCitizen(key="city-not-a-citizen-anywhere")
    if entry.leaving_at is not None:
        return entry
    #: The obligation taken at printing (D-184) holds until its term. It holds
    #: the person, not the city: exile cuts it at any moment.
    if entry.bound_until is not None and entry.bound_until > moment:
        raise Bound(key="city-bound-by-printing", until=f"{entry.bound_until:%d.%m %H:%M}")

    entry.leaving_at = moment + timedelta(days=constants[R.CITY_EXIT_DELAY])
    await session.flush()
    event = await events.record(
        session,
        EventKind.CITIZENSHIP_LEAVING,
        actor_identity_id=identity.id,
        city_id=str(entry.city_id),
        leaves_at=entry.leaving_at.isoformat(),
    )
    await enqueue(
        session,
        JobKind.CITIZENSHIP_EXIT,
        entry.leaving_at,
        payload={"citizen": str(entry.id)},
        dedup_key=f"citizenship.exit:{entry.id}",
        cause_event_id=event.id,
    )
    return entry


async def exile(session: AsyncSession, by: Identity, city: City, who: Identity) -> None:
    """Exile from the city. A sanction, not a personnel decision: right `justice`.

    The charter options `court` and `citizens_vote` are not enforced while there
    is no court and no polls: the engine checks the right, and who holds it is
    the city's business.
    """
    await require(session, by.id, city, Power.JUSTICE)
    entry = await citizenship(session, who.id)
    if entry is None or entry.city_id != city.id:
        raise NotCitizen(key="city-not-a-citizen-here", who=who.name)
    await session.delete(entry)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_ENDED,
        actor_identity_id=by.id,
        city_id=str(city.id),
        who=who.name,
        why="expelled",
    )


async def _enroll(
    session: AsyncSession,
    city: City,
    identity_id: uuid.UUID,
    *,
    why: str,
    by: uuid.UUID | None = None,
    bound_until: datetime | None = None,
) -> Citizen:
    entry = Citizen(identity_id=identity_id, city_id=city.id, bound_until=bound_until)
    session.add(entry)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_GRANTED,
        actor_identity_id=by or identity_id,
        node_id=city.node_id,
        city_id=str(city.id),
        whom_identity_id=str(identity_id),
        how=why,
        bound_until=None if bound_until is None else bound_until.isoformat(),
    )
    return entry


@handler(JobKind.CITIZENSHIP_EXIT)
async def exited(session: AsyncSession, job: Job) -> None:
    """The term is up: citizenship lapses (D-160).

    The declaration could have been withdrawn -- then the record is gone or the
    term is cleared, and the job does nothing: a retry after a failure does not
    become a second exit.
    """
    entry = await session.get(Citizen, uuid.UUID(job.payload["citizen"]))
    if entry is None or entry.leaving_at is None:
        return
    city = await by_id(session, entry.city_id)
    await session.delete(entry)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_ENDED,
        actor_identity_id=entry.identity_id,
        city_id=str(entry.city_id),
        why="resigned",
        city=None if city is None else city.name,
    )


async def _enrol_founder(session: AsyncSession, city: City, who: Identity) -> None:
    """Make the founder a citizen of the city they have just founded (D-195)."""
    entry = await citizenship(session, who.id)
    if entry is not None:
        if entry.city_id == city.id:
            return
        #: One citizenship per person: the previous one ends here and now.
        await session.delete(entry)
        await session.flush()
        await events.record(
            session,
            EventKind.CITIZENSHIP_ENDED,
            actor_identity_id=who.id,
            city_id=str(entry.city_id),
            #: `why`, like every other end of a citizenship: this was the one
            #: writer calling the same field `reason`.
            why="founded_own_city",
        )

    session.add(Citizen(identity_id=who.id, city_id=city.id))
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_GRANTED,
        actor_identity_id=who.id,
        node_id=city.node_id,
        city_id=str(city.id),
        founder=True,
    )


async def bind(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    city: City,
    who: Identity,
    *,
    now: datetime | None = None,
) -> Citizen | None:
    """Fulfil the print conditions: enrol as a citizen for a term (D-184).

    No admission is needed: the person consented by choosing the door, and there
    is no reason to ask twice. The term **is written here** rather than read
    from the law later: a city that raises the term retroactively does not
    lengthen an obligation already taken.

    Does nothing if the city sets no conditions or the person already belongs
    somewhere: a print may not fail over a personnel question.
    """
    required, days = spawn_terms(constants, catalog, city)
    if not required:
        return None
    if await citizenship(session, who.id) is not None:
        return None

    moment = now or datetime.now(UTC)
    return await _enroll(
        session,
        city,
        who.id,
        why="printed",
        bound_until=None if days <= 0 else moment + timedelta(days=days),
    )


async def describe(
    session: AsyncSession, by: Identity, city: City, text: str, *, body=None
) -> City:
    """Write the city's word to newcomers -- what stands on the door card (D-183).

    It is edited by whoever admits citizens (D-160): the announcement is
    recruitment, and whoever answers for the inflow of people should control
    it, not the treasurer. In person, like every city decision (D-155).

    The engine **does not parse** what is written and executes nothing from it.
    "A plot for everyone" is a promise, not a code-law; if it is not kept that
    is a lawsuit (D-004), not an engine error. Otherwise we would have to
    either read promises with code or forbid them altogether, leaving the city
    without a voice.
    """

    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.CITIZENS)

    word = text.strip()
    if len(word) > CITY_ABOUT_LIMIT:
        raise CityError(key="city-about-too-long", limit=CITY_ABOUT_LIMIT)

    before, city.about = city.about, word
    await session.flush()
    await events.record(
        session,
        EventKind.CITY_DESCRIBED,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        was=before,
        now=word,
    )
    return city
