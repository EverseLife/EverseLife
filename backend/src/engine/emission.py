# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Emission by signatures (D-270): the capital prints money into its treasury.

Money is printed by the capital alone (D-175), and until now only the bank's
own arithmetic printed it -- the shortfall of a loan, the works fund under
its cap (D-248). This is the deliberate kind: a holder of the `emission`
right proposes a sum, the others holding the right sign, and once the
signatures reach `emission.signature_share` of all holders the sum lands in
the capital's treasury from `genesis`. One holder alone prints at once; four
need two; a lone proposer among three waits for one more hand.

What keeps it honest:

* the right is void anywhere but the capital -- the flag is the seed's, and
  a city founded by players cannot give itself a mint by naming an office;
* one live proposal per city, and it lives `emission.proposal_hours`: an
  unsigned sum does not hang over the treasury for ever, and the next
  proposal at the counter marks it expired;
* a hand counts while it holds the right: a signature of somebody since
  dismissed is not a signature, and a holder whose hand already stands may
  put it down again to have the count taken afresh -- the proposal is not
  left to hang when the hands around it changed;
* what is printed enters the emission share like a loan's shortfall does
  (`bank._emission_share`), so the key-rate formula sees the tap (D-030);
* the proposal row is taken for the transaction by every signature: two last
  hands cannot both close it, and the treasury gains the sum once.

Decisions are made in the administration, in person (D-155), like every other.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import events, ledger
from src.engine.errors import Refusal
from src.models.city import City, Power
from src.models.emission import EmissionProposal, EmissionSignature, EmissionState
from src.models.event import EventKind
from src.models.identity import Body, Identity
from src.models.ledger import AccountKind, PostingReason
from src.units import PERCENT, money, money_str


class EmissionError(Refusal):
    """The counter refuses. An in-game situation, not a server error."""


class NotCapital(EmissionError):
    """The right is void here: only the capital prints (D-175, D-270)."""


class NoProposal(EmissionError):
    """Nothing to sign: no such proposal, or not this city's."""


class AlreadySigned(EmissionError):
    """A hand signs once."""


async def holders_of(session: AsyncSession, city: City) -> list[uuid.UUID]:
    """Who holds the right in this city, one entry a person whatever the offices."""
    found: set[uuid.UUID] = set()
    for office in await town.offices(session, city):
        if Power.EMISSION.value in (office.powers or ()):
            found.add(office.identity_id)
    return sorted(found)


def needed_of(constants: Constants, holders: int) -> int:
    """How many signatures print: the vault's share of the holders, at least one."""
    share = constants[R.EMISSION_SIGNATURE_SHARE]
    return max(1, math.ceil(holders * share / PERCENT))


async def open_of(session: AsyncSession, city: City, *, now: datetime) -> EmissionProposal | None:
    """The proposal collecting signatures right now, if any. A read: an expired
    row is simply not it, marking it is the next action's business."""
    return (
        await session.execute(
            select(EmissionProposal).where(
                EmissionProposal.city_id == city.id,
                EmissionProposal.state == EmissionState.OPEN,
                EmissionProposal.expires_at > now,
            )
        )
    ).scalar_one_or_none()


async def signers_of(session: AsyncSession, proposal: EmissionProposal) -> list[uuid.UUID]:
    """Whose hands stand under the proposal, whatever they hold today."""
    return list(
        (
            await session.execute(
                select(EmissionSignature.identity_id).where(
                    EmissionSignature.proposal_id == proposal.id
                )
            )
        ).scalars()
    )


async def _signed_by(
    session: AsyncSession, proposal: EmissionProposal, identity_id: uuid.UUID
) -> bool:
    return (
        await session.scalar(
            select(EmissionSignature.id).where(
                EmissionSignature.proposal_id == proposal.id,
                EmissionSignature.identity_id == identity_id,
            )
        )
    ) is not None


async def _expire_stale(session: AsyncSession, city: City, *, now: datetime) -> None:
    """Mark what ran out of time: the open-proposal index holds one live row
    per city, and a dead one must not stand in the way of the next."""
    await session.execute(
        update(EmissionProposal)
        .where(
            EmissionProposal.city_id == city.id,
            EmissionProposal.state == EmissionState.OPEN,
            EmissionProposal.expires_at <= now,
        )
        .values(state=EmissionState.EXPIRED)
    )


async def propose(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    body: Body,
    amount: float,
    *,
    now: datetime | None = None,
) -> EmissionProposal:
    """Propose to print this much into the treasury; the proposer's hand is the first signature.

    In person, at the administration (D-155), by the right (D-154), and only
    in the capital (D-175). The city row is taken for the transaction: two
    proposers at once must not both find the counter empty.
    """
    moment = now or datetime.now(UTC)
    await town.require_at_hall(session, body, city)
    await town.require(session, by.id, city, Power.EMISSION)
    if not city.capital:
        raise NotCapital(key="emission-not-capital", city=city.name)
    total = money(amount)
    if total <= 0:
        raise EmissionError(key="emission-not-positive")

    await session.execute(select(City.id).where(City.id == city.id).with_for_update())
    live = await open_of(session, city, now=moment)
    if live is not None:
        raise EmissionError(key="emission-proposal-open", money=money_str(live.amount))
    await _expire_stale(session, city, now=moment)

    proposal = EmissionProposal(
        city_id=city.id,
        proposer_identity_id=by.id,
        amount=total,
        state=EmissionState.OPEN,
        expires_at=moment + timedelta(hours=constants[R.EMISSION_PROPOSAL_HOURS]),
    )
    session.add(proposal)
    await session.flush()
    session.add(EmissionSignature(proposal_id=proposal.id, identity_id=by.id))
    await session.flush()
    await events.record(
        session,
        EventKind.EMISSION_PROPOSED,
        actor_identity_id=by.id,
        node_id=body.node_id,
        city_id=str(city.id),
        city=city.name,
        proposal=str(proposal.id),
        amount=total,
        money=money_str(total),
    )
    await _settle(session, constants, city, body, proposal, now=moment)
    return proposal


async def sign(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    body: Body,
    proposal_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> EmissionProposal:
    """Put a hand under the proposal; the one that completes the share prints.

    The proposal row is locked for the transaction: the last two hands
    serialise on it, and the second finds the sum already printed.
    """
    moment = now or datetime.now(UTC)
    await town.require_at_hall(session, body, city)
    await town.require(session, by.id, city, Power.EMISSION)
    proposal = await session.get(
        EmissionProposal, proposal_id, with_for_update=True, populate_existing=True
    )
    if proposal is None or proposal.city_id != city.id:
        raise NoProposal(key="emission-no-proposal")
    if proposal.state is not EmissionState.OPEN:
        raise EmissionError(key="emission-proposal-closed")
    if proposal.expires_at <= moment:
        raise EmissionError(key="emission-proposal-expired")
    if await _signed_by(session, proposal, by.id):
        #: The hand already stands; the count is taken afresh all the same --
        #: the hands around it may have been dismissed since, and a proposal
        #: that the remaining holders already carry must not hang until it
        #: expires. Printed -- the hand did its work; not -- it stood already.
        if await _settle(session, constants, city, body, proposal, now=moment):
            return proposal
        raise AlreadySigned(key="emission-already-signed")

    session.add(EmissionSignature(proposal_id=proposal.id, identity_id=by.id))
    await session.flush()
    await events.record(
        session,
        EventKind.EMISSION_SIGNED,
        actor_identity_id=by.id,
        node_id=body.node_id,
        city_id=str(city.id),
        city=city.name,
        proposal=str(proposal.id),
        money=money_str(proposal.amount),
    )
    await _settle(session, constants, city, body, proposal, now=moment)
    return proposal


async def _settle(
    session: AsyncSession,
    constants: Constants,
    city: City,
    body: Body,
    proposal: EmissionProposal,
    *,
    now: datetime,
) -> bool:
    """Print if the hands are enough. Returns whether it did."""
    holders = await holders_of(session, city)
    signed = len(set(await signers_of(session, proposal)) & set(holders))
    needed = needed_of(constants, len(holders))
    if signed < needed:
        return False
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    treasury = await town.treasury(session, city)
    await ledger.transfer(
        session,
        PostingReason.EMISSION,
        debit=genesis.id,
        credit=treasury.id,
        amount=proposal.amount,
        memo={"proposal": str(proposal.id), "signatures": signed, "holders": len(holders)},
    )
    proposal.state = EmissionState.PRINTED
    proposal.printed_at = now
    await session.flush()
    await events.record(
        session,
        EventKind.EMISSION_PRINTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        city_id=str(city.id),
        city=city.name,
        proposal=str(proposal.id),
        amount=proposal.amount,
        money=money_str(proposal.amount),
        signatures=signed,
        holders=len(holders),
    )
    return True


async def view(
    session: AsyncSession,
    constants: Constants,
    city: City,
    identity_id: uuid.UUID,
    *,
    now: datetime,
) -> dict:
    """The counter as the window shows it: how many hands there are and how
    many print, and the live proposal if one stands. A read: nothing is
    marked, nothing is made."""
    holders = await holders_of(session, city)
    out: dict = {"holders": len(holders), "needed": needed_of(constants, len(holders))}
    live = await open_of(session, city, now=now)
    if live is not None:
        proposer = await session.get(Identity, live.proposer_identity_id)
        out["proposal"] = {
            "id": str(live.id),
            "money": live.amount,
            "who": "?" if proposer is None else proposer.name,
            #: Hands that count today, not every hand that was ever put down.
            "signed": len(set(await signers_of(session, live)) & set(holders)),
            "expires_at": live.expires_at.isoformat(),
            "mine": await _signed_by(session, live, identity_id),
        }
    return out
