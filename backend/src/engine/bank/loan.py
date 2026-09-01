# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The loan itself (D-030, D-063, D-174): borrowing against the limit,
interest accrued and repaid in its order, insolvency that holds the debtor
in the node, collection, and the prison face that repays a debt.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import events, ledger
from src.engine.bank._base import BankError, NothingToRepay, TooMuch, key_rate, reserve_account
from src.engine.bank.line import city_line, city_margin
from src.engine.bank.trust import personal_turnover, repaid_total, trust
from src.engine.errors import Says
from src.models.bank import Loan, LoanState
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.ledger import (
    AccountKind,
    PostingReason,
)
from src.units import MONEY_SCALE, PERCENT, money, money_str


async def borrow(
    session: AsyncSession,
    constants: Constants,
    catalog,
    who: Identity,
    amount: float,
    *,
    now: datetime | None = None,
) -> Loan:
    """Take a loan. Money comes from the reserve; the shortfall is printed (D-087).

    The loan goes through the city of citizenship (D-175): the rate is key plus
    city margin, and the loan takes up the city's credit line with the capital.
    No citizenship or line exhausted -- a direct loan from the capital at the
    worse rate.
    """

    moment = now or datetime.now(UTC)
    total = money(amount)
    if total <= 0:
        raise BankError(key="bank-loan-not-positive")

    limit_, reason = await credit_limit(session, constants, who.id, now=moment)
    available = limit_ - await debt_of(session, who.id)
    if total > available:
        raise TooMuch(
            key="bank-over-limit",
            available=money_str(max(0, available)),
            limit=money_str(limit_),
            #: What the limit is made of is a quoted message, not a Russian
            #: string handed over as an argument (D-251 wave IV): the same
            #: clauses the bank window shows, said in the reader's language.
            inner={"reason": reason},
        )

    #: The city of citizenship and its line. The line shrinks smoothly: exactly
    #: the remainder is available, and a "take everything before the cutoff"
    #: run hits arithmetic.
    city = None
    margin = 0.0
    entry = await town.citizenship(session, who.id)
    if entry is not None:
        candidate = await town.by_id(session, entry.city_id)
        if candidate is not None:
            _, _, free = await city_line(session, constants, candidate, now=moment)
            if total <= free:
                city = candidate
                margin = city_margin(constants, catalog, candidate)

    if city is not None:
        rate_value = await key_rate(session, constants) + margin
    else:
        #: A direct loan from the capital: the way out for non-citizens and
        #: residents of cut-off cities, but at the top of the risk range (D-175).
        rate_value = await key_rate(session, constants) + constants[R.BANK_RISK_PREMIUM].max

    #: The reserve is a steriliser: first we spend already existing TC, and
    #: print only the shortfall. Printing shows as a separate posting and in telemetry.
    reserve_treasury = await reserve_account(session)
    have = await ledger.balance(session, reserve_treasury.id)
    printed = max(0, total - have)
    if printed > 0:
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=reserve_treasury.id,
            amount=printed,
            memo={"печать под кредит": who.name},
        )

    account = await ledger.account_for(session, AccountKind.IDENTITY, who.id)
    await ledger.transfer(
        session,
        PostingReason.LOAN,
        debit=reserve_treasury.id,
        credit=account.id,
        amount=total,
        memo={"кредит": who.name},
    )

    loan = Loan(
        identity_id=who.id,
        principal=total,
        outstanding=total,
        rate=rate_value,
        city_id=None if city is None else city.id,
        margin=margin,
        printed=printed,
        taken_at=moment,
        accrued_at=moment,
        serviced_at=moment,
    )
    session.add(loan)
    await session.flush()
    await events.record(
        session,
        EventKind.LOAN_TAKEN,
        actor_identity_id=who.id,
        loan_id=str(loan.id),
        amount=total,
        rate=rate_value,
        printed=printed,
        city=None if city is None else city.name,
        margin=margin,
    )
    return loan


def accruable(constants: Constants, loan: Loan, *, now: datetime | None = None) -> int:
    """Interest run up since the last accrual, without writing it: what a
    view shows as owed right now. `accrue` writes it on repayment and on the
    daily collection; a read must not (review 2026-08-23)."""
    moment = now or datetime.now(UTC)
    if loan.state is not LoanState.OPEN:
        return 0
    elapsed = (moment - loan.accrued_at).total_seconds() / timedelta(days=1).total_seconds()
    if elapsed <= 0:
        return 0
    per_day = float(loan.rate) / PERCENT / constants[R.BANK_YEAR_DAYS]
    return int(loan.outstanding * per_day * elapsed)


async def accrue(
    session: AsyncSession, constants: Constants, loan: Loan, *, now: datetime | None = None
) -> int:
    """Accrue interest for the past day. Returns the accrued amount.

    There is no year in the world -- Terra's day is thirty hours -- so the
    accounting year is set by the vault (`bank.year_days`, D-167): a banking
    number, not an astronomical one.
    """
    moment = now or datetime.now(UTC)
    accrued = accruable(constants, loan, now=moment)
    if accrued <= 0:
        return 0
    loan.outstanding += accrued
    loan.interest_accrued += accrued
    loan.accrued_at = moment
    await session.flush()
    return accrued


async def repay(
    session: AsyncSession,
    constants: Constants,
    who: Identity,
    loan: Loan,
    amount: float | None = None,
    *,
    from_account=None,
    now: datetime | None = None,
) -> int:
    """Repay debt. Money goes **to the reserve**, not into circulation (D-087).

    **Anyone** may pay (D-063, D-168): a third party may settle for the debtor
    -- and a city from its treasury too (`from_account`). The engine does not
    ask why: money accepted, debt reduced.
    """
    moment = now or datetime.now(UTC)
    if loan.state is not LoanState.OPEN:
        raise NothingToRepay(key="bank-loan-closed")
    await accrue(session, constants, loan, now=moment)

    account = from_account or await ledger.account_for(session, AccountKind.IDENTITY, who.id)
    have = await ledger.balance(session, account.id)
    wants = loan.outstanding if amount is None else money(amount)
    payment = min(wants, loan.outstanding, have)
    if payment <= 0:
        raise NothingToRepay(key="bank-nothing-to-pay-with")

    await _settle(session, loan, account, payment)
    loan.serviced_at = moment
    if loan.outstanding <= 0:
        loan.state = LoanState.REPAID
        loan.repaid_at = moment
    await session.flush()
    await events.record(
        session,
        EventKind.LOAN_REPAID,
        actor_identity_id=who.id,
        loan_id=str(loan.id),
        amount=payment,
        left=loan.outstanding,
        closed=loan.state is LoanState.REPAID,
    )
    return payment


async def _settle(session: AsyncSession, loan: Loan, account, payment: int) -> None:
    """Post a payment: interest ahead of principal, city margin to its treasury.

    The usual banking order, and it also makes "system income" measurable
    (D-171): without separate accounting the city margin cannot be separated
    from the key part that is sterilised in the capital's reserve (D-175).
    """

    interest = min(payment, max(0, loan.interest_accrued - loan.interest_paid))
    city_margin = 0
    if interest > 0 and loan.city_id is not None and float(loan.rate) > 0:
        city_margin = int(interest * float(loan.margin) / float(loan.rate))
    city = None if loan.city_id is None else await town.by_id(session, loan.city_id)

    if city_margin > 0 and city is not None:
        await ledger.transfer(
            session,
            PostingReason.BANK_MARGIN,
            debit=account.id,
            credit=(await town.treasury(session, city)).id,
            amount=city_margin,
            memo={"маржа города": city.name, "заём": str(loan.id)},
        )
    to_reserve = payment - city_margin
    if to_reserve > 0:
        await ledger.transfer(
            session,
            PostingReason.LOAN_REPAYMENT,
            debit=account.id,
            credit=(await reserve_account(session)).id,
            amount=to_reserve,
            memo={"погашение": str(loan.id)},
        )
    loan.interest_paid += interest
    loan.outstanding -= payment


async def loans_of(session: AsyncSession, identity_id: uuid.UUID) -> list[Loan]:
    return list(
        (
            await session.execute(
                select(Loan).where(Loan.identity_id == identity_id, Loan.state == LoanState.OPEN)
            )
        )
        .scalars()
        .all()
    )


def overdue_days(loan: Loan, now: datetime) -> float:
    """How many days the loan went unpaid. Overdue means non-payment, not age."""
    return (now - loan.serviced_at).total_seconds() / timedelta(days=1).total_seconds()


def overdue(constants: Constants, loan: Loan, now: datetime) -> bool:
    return overdue_days(loan, now) > constants[R.DEBT_GRACE_PERIOD]


async def debt_of(session: AsyncSession, identity_id: uuid.UUID) -> int:
    """The identity's whole outstanding debt, in minor units."""
    return sum(loan.outstanding for loan in await loans_of(session, identity_id))


async def restrained(
    session: AsyncSession,
    constants: Constants,
    identity_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> Loan | None:
    """Whether debt holds this person in the node (D-063).

    Two conditions at once: the debt exceeds everything on the account, and it
    has not been serviced for longer than `debt.prison_threshold`. One is not
    enough: a loan a person honestly repays does not take freedom, and poverty
    by itself is not a crime.
    """
    moment = now or datetime.now(UTC)
    loans = await loans_of(session, identity_id)
    if not loans:
        return None
    debt = sum(loan.outstanding for loan in loans)
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity_id)
    if debt <= await ledger.balance(session, account.id):
        return None
    threshold = constants[R.DEBT_PRISON_THRESHOLD]
    overdue = [loan for loan in loans if overdue_days(loan, moment) > threshold]
    if not overdue:
        return None
    return max(overdue, key=lambda loan: overdue_days(loan, moment))


async def collect(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> int:
    """Forced withholding from overdue debts. Returns what was withheld.

    The vault says "percent of income", but income as a measurable quantity
    does not exist: money comes to the account from sales, from the treasury
    and from gifts, and they cannot be separated. Withholding from the balance
    is the nearest honest approximation, and the behaviour is the same: a
    working debtor's debt melts, an idle one's does not (D-168).
    """
    moment = now or datetime.now(UTC)
    share = constants[R.DEBT_WORKOFF_RATE] / PERCENT
    withheld = 0

    loans = (
        (await session.execute(select(Loan).where(Loan.state == LoanState.OPEN))).scalars().all()
    )
    for loan in loans:
        #: A treasury loan (D-248) has no identity to withhold from: the
        #: city's discipline is its line -- occupied until repaid.
        if loan.identity_id is None:
            continue
        if not overdue(constants, loan, moment):
            continue
        await accrue(session, constants, loan, now=moment)
        account = await ledger.account_for(session, AccountKind.IDENTITY, loan.identity_id)
        have = await ledger.balance(session, account.id)
        payment = min(int(have * share), loan.outstanding)
        if payment <= 0:
            continue

        await _settle(session, loan, account, payment)
        #: Withholding is not a payment by the debtor: it does not reset the
        #: overdue, otherwise the insolvent would hang in the grace period forever.
        if loan.outstanding <= 0:
            loan.state = LoanState.REPAID
            loan.repaid_at = moment
        withheld += payment
        await events.record(
            session,
            EventKind.DEBT_WITHHELD,
            actor_identity_id=loan.identity_id,
            loan_id=str(loan.id),
            amount=payment,
            left=loan.outstanding,
        )
    await session.flush()
    return withheld


async def credit_limit(
    session: AsyncSession,
    constants: Constants,
    identity_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> tuple[int, list[Says]]:
    """Credit limit and what it is made of -- public, like the rate (D-030).

    Base from the vault, plus a share of sales turnover, plus a share of what
    was repaid, times trust and record. Labour, not the calendar: time in game
    is the cheapest thing to farm (D-173).

    The parts are named rather than worded (D-251 wave IV): the same list is
    read by the bank window and quoted by the refusal of too large a loan, and
    both must say it in the language of whoever is looking.
    """
    moment = now or datetime.now(UTC)
    base_ = money(constants[R.BANK_UNSECURED_LIMIT])
    turnover = await personal_turnover(session, constants, identity_id, now=moment)
    returned_ = await repaid_total(session, identity_id)
    limit_ = (
        base_
        + int(turnover * constants[R.CREDIT_TURNOVER_SHARE] / PERCENT)
        + int(returned_ * constants[R.CREDIT_REPAID_SHARE] / PERCENT)
    )
    #: Money travels as a formatted string (D-190): the sentence puts it in
    #: as it was written, it does not write it itself.
    reasons = [
        Says("bank-why-limit-base", {"money": money_str(base_)}),
        Says(
            "bank-why-limit-turnover",
            {"money": money_str(turnover), "days": constants[R.CREDIT_WINDOW]},
        ),
        Says("bank-why-limit-repaid", {"money": money_str(returned_)}),
    ]

    #: The record is a multiplier, not a base: a bonus for a history without overdue.
    loans = await loans_of(session, identity_id)
    no_overdue = not any(overdue(constants, loan, moment) for loan in loans)
    if returned_ > 0 and no_overdue:
        limit_ = int(limit_ * (1 + constants[R.CREDIT_NO_OVERDUE_BONUS] / PERCENT))
        reasons.append(Says("bank-why-limit-no-overdue"))

    faith = await trust(session, constants, identity_id)
    if faith < 1:
        limit_ = int(limit_ * faith)
        reasons.append(Says("bank-why-limit-trust", {"trust": faith * PERCENT}))
    return limit_, reasons


# --- prison credit (D-174) ---------------------------------------------------


async def prison_credit(
    session: AsyncSession,
    constants: Constants,
    city,
    debtor_identity_id: uuid.UUID,
    cost: int,
    *,
    now: datetime | None = None,
) -> int:
    """The treasury pays the reference value of what a prisoner mined toward their debt.

    The circle closes (D-174, D-175): ore to the city, treasury money toward
    repayment, repayment to the capital's reserve. Returns how much could be
    credited; zero -- the treasury is empty, and the ore stays with the prisoner.
    """

    moment = now or datetime.now(UTC)
    treasury = await town.treasury(session, city)
    if await ledger.balance(session, treasury.id) < cost:
        return 0

    debtor = await session.get(Identity, debtor_identity_id)
    loans = sorted(await loans_of(session, debtor_identity_id), key=lambda loan: loan.taken_at)
    credited = 0
    remainder = cost
    for loan in loans:
        if remainder <= 0:
            break
        payment = min(remainder, loan.outstanding)
        if payment <= 0:
            continue
        await repay(
            session,
            constants,
            debtor,
            loan,
            payment / MONEY_SCALE,
            from_account=treasury,
            now=moment,
        )
        credited += payment
        remainder -= payment
    if credited > 0:
        await events.record(
            session,
            EventKind.PRISON_WORKOFF,
            actor_identity_id=debtor_identity_id,
            city_id=str(city.id),
            amount=credited,
        )
    return credited
