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

from src import i18n
from src.api.commands.common import _identity, _node, speaks
from src.api.commands.views import _city, _money
from src.api.registry import Refused, command
from src.constants import current, current_catalog
from src.constants import registry as R
from src.engine import (
    bank,
    finance,
    utility,
    works,
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


def _archived(decision: RateDecision | None, *, locale: str) -> list[str]:
    """A stored decision's reasons, said to this reader (D-251 wave IV).

    New rows keep keys and are said in the reader's language. Rows written
    before the column existed keep only the Russian line rendered at the
    moment of the decision -- shown as the single clause it is, because an
    audit trail with a hole in it is worse than one line in the wrong
    language. Nothing writes that column any more, so the fallback empties
    itself as the history rolls forward.
    """
    if decision is None:
        return []
    said = i18n.retold(decision.why_said, locale=locale)
    return said or ([decision.why] if decision.why else [])


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
        #: Clause by clause, not one sentence with punctuation to be split
        #: back apart: the panel draws a list, and a Russian decimal comma
        #: inside a number is not a separator (D-251 wave IV).
        "why": _archived(decision, locale=speaks(state)),
        #: Reserve, circulation and the works fund are public: monetary policy
        #: is never secret (D-030, D-248).
        "reserve": await bank.reserve(db),
        "circulating": await bank.circulating(db),
        "fund": await works.fund_balance(db),
        #: The limit is a public formula from labour (D-173): the player sees
        #: both the number and what it is made of before going for a loan.
        "limit": (limits := await bank.credit_limit(db, constants, state["identity_id"]))[0],
        #: The clauses are said here, in the reader's language (D-251 wave IV):
        #: the engine names them, the wire carries them as the list they are.
        "limit_why": i18n.clauses(limits[1], locale=speaks(state)),
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
        "your_rate_why": i18n.clauses(offer[1], locale=speaks(state)),
        "loans": loans,
    }


@command("works.board")
async def _works_board(state: dict, db: AsyncSession, message: dict) -> dict:
    """The state order board (D-248). Remote: the board is public reference."""
    return {"orders": await works.board(db), "fund": await works.fund_balance(db)}


@command("bank.borrow")
async def _bank_borrow(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take a loan. The money is the city's own, out of its treasury (D-283)."""
    identity = await _identity(state, db)
    loan = await bank.borrow(db, current(), current_catalog(), identity, float(message["amount"]))
    return {"loan": str(loan.id), "rate": float(loan.rate)}


@command("bank.repay")
async def _bank_repay(state: dict, db: AsyncSession, message: dict) -> dict:
    """Repay debt. Money goes to the reserve, not into circulation."""

    identity = await _identity(state, db)
    loan = await db.get(Loan, uuid.UUID(message["loan"]))
    if loan is None or loan.identity_id != identity.id:
        raise Refused(key="cmd-no-such-loan")
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

    recommendation, reasons = bank.compute_rate(
        constants,
        previous=await bank.key_rate(db, constants),
        inflation=await bank.inflation(db, constants),
        emission_share=await bank._emission_share(db, constants, now=_now()),
    )
    until = await bank.locked_until(db)
    return {
        "council_decides": await bank.council_decides(db, constants),
        "cities_with_hall": await bank.cities_with_hall(db),
        "handover_at": constants[R.BANK_COUNCIL_HANDOVER_CITIES],
        "advised": recommendation,
        "why": i18n.clauses(reasons, locale=speaks(state)),
        "corridor": constants[R.BANK_COUNCIL_RATE_DEVIATION],
        "locked_until": None if until is None else until.isoformat(),
    }


@command("bank.council_rate")
async def _bank_council_rate(state: dict, db: AsyncSession, message: dict) -> dict:
    """The city's vote on the rate. Cast by the holder of the `laws` right (D-172)."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    decision = await bank.council_set_rate(db, current(), city, identity, float(message["rate"]))
    return {"rate": float(decision.rate), "why": _archived(decision, locale=speaks(state))}
