# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Citizenship: who belongs to a city, and how they come and go (D-160, D-281).

One citizenship per person. It begins in one of three ways -- the door a
newcomer chose gives it outright, the city admits by its charter, or a founder
takes his own city's -- and the engine holds the rule that there is exactly
one, refusing everything that would make a second. The door is the usual way
now: a person who never chose to belong anywhere is the exception, not the
rule (D-281).

Going is the mirror of that and just as short: one asks, and it is over --
except while a loan is open. The city answers for its borrowers on the
capital's line (D-175), so the debtor settles up first; nothing else holds,
and no delay is served.

Three functions live here that used to sit elsewhere, and the moves are the
whole reason this file is a file. `_enrol_founder` was among the offices,
because a founder is given a post at the same moment; the door's enrolment was
among the laws, because it used to read the charter's print conditions;
`describe` was there too, though it reads no law at all -- it writes the
city's own text and is guarded by the right to admit people. Leaving the first
two where they were is what made the city's four sections a cycle instead of a
stack: offices reached down into citizenship, laws reached down into
citizenship, and citizenship reached back up into both.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import events, travel
from src.engine.city._base import (
    CityError,
    NotAllowed,
)
from src.engine.city.hall import require_at_hall
from src.engine.city.lookup import by_id
from src.engine.city.office import require

#: The loan table read straight from the models, and that is the layering
#: rather than a shortcut: the exit asks the bank a question (`is anything
#: owed`), but `engine.bank` already names `engine.city` -- an import back
#: would close a cycle for one `select`. Models sit below both.
from src.models.bank import Loan, LoanState
from src.models.city import (
    Citizen,
    CitizenshipRequest,
    City,
    Power,
)
from src.models.event import EventKind
from src.models.identity import Identity
from src.runtime import CITY_ABOUT_LIMIT

#: The charter question "how are citizens admitted" and its options (`laws.json`).
ADMISSION = "citizenship_admission"


OPEN, APPLICATION, INVITE = "open", "application", "invite"


class NotCitizen(CityError):
    """This is for citizens. Who exactly is decided by the city, not the engine."""


class AlreadyCitizen(CityError):
    """One citizenship per person: leave the previous city first."""


class InDebt(CityError):
    """A loan is open: one does not leave the city owing it money (D-281).

    The city answers for its borrowers on its line with the capital (D-175),
    and a citizenship dropped over an open loan would leave the city holding
    the debt of somebody who is no longer its business.
    """


def admission(city: City) -> str:
    """How this city admits citizens: the charter's answer, or "open"."""
    return str((city.charter or {}).get(ADMISSION) or OPEN)


async def citizenship(
    session: AsyncSession, identity_id: uuid.UUID, *, hold: bool = False
) -> Citizen | None:
    """The identity's citizenship, if any. There is one -- that is how the record works.

    `hold` locks the row for the transaction, and only two callers ask for it:
    leaving, which deletes it, and borrowing, which must not be issued to
    somebody walking out the door. Reading does not write and does not lock
    (D-225): the digest asks this on every look.
    """
    stmt = select(Citizen).where(Citizen.identity_id == identity_id)
    if hold:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def owes(session: AsyncSession, identity_id: uuid.UUID) -> bool:
    """Whether this person has a loan still open. The exit's one condition (D-281)."""
    return (
        await session.execute(
            select(Loan.id)
            .where(Loan.identity_id == identity_id, Loan.state == LoanState.OPEN)
            .limit(1)
        )
    ).first() is not None


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


async def leave(session: AsyncSession, identity: Identity) -> City | None:
    """Leave the city. At once, and the only thing that holds is a debt (D-281).

    Remote: the word goes over the Net, like a vote (D-161) -- belonging is a
    record about the person, and the person does not have to walk to the hall
    to stop being of a city.

    The exit used to wait out `city.exit_delay` so that one could not walk out
    of a city right before a verdict. There is no court in the world yet, and
    the delay held the honest as well; what holds now is the loan: the city
    answers for its borrowers on the capital's line (D-175), and the debtor
    settles up before leaving. When the court arrives, an open case stands
    here beside the open loan.

    The row is taken under the transaction: `borrow` reads the same one held,
    so a loan and an exit cannot cross -- whichever comes second sees the first.
    """

    entry = await citizenship(session, identity.id, hold=True)
    if entry is None:
        raise NotCitizen(key="city-not-a-citizen-anywhere")
    if await owes(session, identity.id):
        raise InDebt(key="city-leave-in-debt")

    #: Read off the row before it goes: after the delete the record is only as
    #: alive as SQLAlchemy's session cares to keep it.
    left = entry.city_id
    city = await by_id(session, left)
    await session.delete(entry)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_ENDED,
        actor_identity_id=identity.id,
        node_id=None if city is None else city.node_id,
        city_id=str(left),
        why="resigned",
        city=None if city is None else city.name,
    )
    return city


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
) -> Citizen:
    entry = Citizen(identity_id=identity_id, city_id=city.id)
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
    )
    return entry


async def _enrol_founder(session: AsyncSession, city: City, who: Identity) -> None:
    """Make the founder a citizen of the city they have just founded (D-195).

    The previous citizenship is **not** ended here any more (D-281): founding
    is entering a citizenship like any other, and one does not enter without
    leaving first. `establish` refuses before a city is written down; this
    refusal is the floor under that one -- a founding that got here with a
    citizenship in hand would be the second one.
    """
    entry = await citizenship(session, who.id)
    if entry is not None:
        if entry.city_id == city.id:
            return
        raise AlreadyCitizen(key="city-found-while-citizen")

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


async def enrol_newcomer(session: AsyncSession, city: City, who: Identity) -> Citizen | None:
    """The door enrols: whoever chose this city is its citizen from the print (D-281).

    No admission is asked and no charter is read: the person consented by
    choosing the door, and a city that admits by invitation still admits
    whoever came out of its own printer -- that door is the invitation.

    Nothing is written any more except the belonging itself. Citizenship used
    to be a print **condition** the city switched on (`spawn_citizenship`) and
    could hold by a term (`spawn_term`, D-184); both are gone with D-281 --
    what the door gives, the debt alone holds.

    Does nothing if the person already belongs somewhere: a print may not fail
    over a personnel question, and it may not make a second citizenship either.
    """
    if await citizenship(session, who.id) is not None:
        return None
    return await _enroll(session, city, who.id, why="printed")


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
