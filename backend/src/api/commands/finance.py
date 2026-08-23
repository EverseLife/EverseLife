# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Bank, transfers, the household meter.

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.common import _identity, _node
from src.api.commands.views import _city, _money
from src.api.registry import Refused, command
from src.constants import current, current_catalog
from src.constants import registry as R
from src.engine import (
    bank,
    finance,
    utility,
)
from src.models.bank import Loan, RateDecision
from src.units import money


@command("finance.statement")
async def _finance_statement(state: dict, db: AsyncSession, message: dict) -> dict:
    """The account statement: the latest operations of this identity (D-190)."""
    return {
        "money": await _money(db, state["identity_id"]),
        "entries": await finance.statement(db, state["identity_id"]),
    }


@command("finance.transfer")
async def _finance_transfer(state: dict, db: AsyncSession, message: dict) -> dict:
    """Send money to another identity. Remote: the account is the Network."""
    identity = await _identity(state, db)
    sent = await finance.transfer(
        db,
        identity,
        str(message.get("to") or ""),
        money(float(message.get("amount") or 0)),
        memo=str(message.get("memo") or ""),
    )
    return {"sent": sent, "money": await _money(db, state["identity_id"])}


@command("utility.holdings")
async def _utility_holdings(state: dict, db: AsyncSession, message: dict) -> dict:
    """Own holdings and household bills. Remote: paying is not done on foot."""
    return {"holdings": await utility.holdings(db, current(), state["identity_id"])}


@command("utility.pay")
async def _utility_pay(state: dict, db: AsyncSession, message: dict) -> dict:
    """Pay off a node's debt and reconnect it."""
    identity = await _identity(state, db)
    node = await _node(db, message["node"])
    paid = await utility.pay(db, current(), identity, node)
    return {"paid": paid, "money": await _money(db, identity.id)}


@command("bank.view")
async def _bank_view(state: dict, db: AsyncSession, message: dict) -> dict:
    """The bank through the player's eyes: the rate with an explanation, own loans, the reserve
    (D-167)."""

    constants = current()
    decision = (
        (await db.execute(select(RateDecision).order_by(RateDecision.decided_at.desc()).limit(1)))
        .scalars()
        .first()
    )
    loans = []
    for loan in await bank.loans_of(db, state["identity_id"]):
        #: Shown with the interest run up so far; written on repayment and
        #: by the daily collection, never by a view.
        loans.append(
            {
                "id": str(loan.id),
                "principal": loan.principal,
                "outstanding": loan.outstanding + bank.accruable(constants, loan),
                "rate": float(loan.rate),
                "taken_at": loan.taken_at.isoformat(),
            }
        )
    return {
        "rate": await bank.key_rate(db, constants),
        "why": None if decision is None else decision.why,
        #: Reserve and circulation are public: monetary policy is never secret (D-030).
        "reserve": await bank.reserve(db),
        "circulating": await bank.circulating(db),
        #: The limit is a public formula from labour (D-173): the player sees
        #: both the number and what it is made of before going for a loan.
        "limit": (limits := await bank.credit_limit(db, constants, state["identity_id"]))[0],
        "limit_why": limits[1],
        #: The rate this borrower would actually get, named before the button
        #: is pressed (D-193): the key rate alone told them nothing.
        "your_rate": (
            offer := await bank.offered_rate(
                db,
                constants,
                current_catalog(),
                await _identity(state, db),
                amount=int(message.get("amount") or 0),
            )
        )[0],
        "your_rate_why": offer[1],
        "loans": loans,
    }


@command("bank.borrow")
async def _bank_borrow(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take a loan. Money comes from the reserve; the shortfall is printed (D-087)."""
    identity = await _identity(state, db)
    loan = await bank.borrow(db, current(), current_catalog(), identity, float(message["amount"]))
    return {"loan": str(loan.id), "rate": float(loan.rate)}


@command("bank.repay")
async def _bank_repay(state: dict, db: AsyncSession, message: dict) -> dict:
    """Repay debt. Money goes to the reserve, not into circulation."""

    identity = await _identity(state, db)
    loan = await db.get(Loan, uuid.UUID(message["loan"]))
    if loan is None or loan.identity_id != identity.id:
        raise Refused("нет такого займа")
    paid = await bank.repay(
        db,
        current(),
        identity,
        loan,
        None if message.get("amount") is None else float(message["amount"]),
    )
    return {"paid": paid, "left": loan.outstanding}


def _now():

    return datetime.now(UTC)


@command("bank.council")
async def _bank_council(state: dict, db: AsyncSession, message: dict) -> dict:
    """Who decides the rate now and in what corridor (D-172). Remote: reference."""
    constants = current()

    recommendation, reason = bank.compute_rate(
        constants,
        previous=await bank.key_rate(db, constants),
        inflation=await bank._inflation(db, constants),
        emission_share=await bank._emission_share(db, constants, now=_now()),
    )
    until = await bank.locked_until(db)
    return {
        "council_decides": await bank.council_decides(db, constants),
        "cities_with_hall": await bank.cities_with_hall(db),
        "handover_at": constants[R.BANK_COUNCIL_HANDOVER_CITIES],
        "advised": recommendation,
        "why": reason,
        "corridor": constants[R.BANK_COUNCIL_RATE_DEVIATION],
        "locked_until": None if until is None else until.isoformat(),
    }


@command("bank.council_rate")
async def _bank_council_rate(state: dict, db: AsyncSession, message: dict) -> dict:
    """The city's vote on the rate. Cast by the holder of the `laws` right (D-172)."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    decision = await bank.council_set_rate(db, current(), city, identity, float(message["rate"]))
    return {"rate": float(decision.rate), "why": decision.why}
