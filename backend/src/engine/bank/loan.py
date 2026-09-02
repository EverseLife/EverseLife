# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The loan itself (D-030, D-063, D-174): borrowing against the limit,
interest accrued and repaid in its order, insolvency that holds the debtor
in the node, collection, and the prison face that repays a debt.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import events, ledger
from src.engine.bank._base import (
    BankError,
    NothingToRepay,
    NotOurs,
    TooMuch,
    key_rate,
    reserve_account,
)
from src.engine.bank.line import city_line, city_margin
from src.engine.bank.trust import interest_paid_total, personal_turnover, trust
from src.engine.errors import Says
from src.models.bank import Loan, LoanState
from src.models.city import City
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.ledger import (
    AccountKind,
    LedgerEntry,
    LedgerTransaction,
    PostingReason,
)
from src.units import MONEY_SCALE, PERCENT, money, money_str

#: One order for taking loan rows, and every place that locks more than one
#: keeps to it: the daily collection (`collect`) and the prison's work-off
#: (`prison_credit`) meet on the same debtor from a worker and from a player's
#: command, and two orders is a deadlock (`Loan.id` is a random uuid, so no
#: business order agrees with it by accident).
LOAN_ORDER = Loan.id


async def lend_to_city(
    session: AsyncSession,
    constants: Constants,
    city,
    total: int,
    *,
    now: datetime | None = None,
    why: str,
    by: uuid.UUID | None = None,
) -> Loan:
    """The capital lends to a city: key rate, no margin, on the city's line (D-248).

    Only the capital prints (D-175), and only here -- since D-283 a citizen's
    loan is paid by their city out of its own treasury, so the one place money
    is made is the level above: the capital lending to a city, reserve first
    and the shortfall printed (D-087).

    The **line is not checked here**. Both callers check it themselves, and
    both must: they hold the city's row for the whole read-then-insert, and
    each has its own word for what is refused -- the ruler asking for a works
    loan hears about the line, a citizen hears that their city cannot fund
    them. A check inside would say one of those two things to both.
    """
    moment = now or datetime.now(UTC)
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
            memo={"printed_for": city.name},
        )
    await ledger.transfer(
        session,
        PostingReason.LOAN,
        debit=reserve_treasury.id,
        credit=(await town.treasury(session, city)).id,
        amount=total,
        memo={"treasury_loan": f"{city.name} · {why}"},
    )
    rate_value = await key_rate(session, constants)
    loan = Loan(
        identity_id=None,
        principal=total,
        outstanding=total,
        rate=rate_value,
        city_id=city.id,
        margin=0,
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
        #: Empty when the loan is taken because a citizen asked: nobody
        #: decided it, the city's own shortfall did.
        actor_identity_id=by,
        loan_id=str(loan.id),
        amount=total,
        rate=rate_value,
        printed=printed,
        city=city.name,
        margin=0,
        treasury_loan=True,
        why=why,
    )
    return loan


async def borrow(
    session: AsyncSession,
    constants: Constants,
    catalog,
    who: Identity,
    amount: float,
    *,
    now: datetime | None = None,
) -> Loan:
    """Take a loan. The money is the city's own, out of its treasury (D-283).

    One borrows from the city one belongs to and from nowhere else (D-281):
    the rate is key plus that city's margin, and what is handed over is the
    city's money. Short of it, the city borrows the difference from the capital
    on its line (D-248) and hands on what it has just been lent -- so the whole
    of the world's printing happens one level up, under a city's own debt, and
    a citizen's loan makes no money at all.

    Three refusals and no fourth: nobody to lend (no citizenship), too much for
    the borrower (the personal limit), too much for the lender (an empty
    treasury and no line). The direct loan from the capital that used to catch
    the last two is gone with D-281.
    """

    moment = now or datetime.now(UTC)
    total = money(amount)
    if total <= 0:
        raise BankError(key="bank-loan-not-positive")

    #: Three remainders, three rows, one direction: person, then their
    #: citizenship, then the city. The free room under the personal limit is a
    #: remainder like a purse, and a loan adds a row rather than changing one,
    #: so what is locked for it is the borrower themselves (CLAUDE.md). The
    #: citizenship is taken held because `city.leave` reads that same row held:
    #: a loan and an exit cannot cross, and the city is never left answering
    #: for the debt of somebody who has already walked out. Nothing anywhere
    #: takes these three the other way round, so nothing deadlocks.
    await session.execute(select(Identity.id).where(Identity.id == who.id).with_for_update())
    entry = await town.citizenship(session, who.id, hold=True)
    if entry is None:
        raise NotOurs(key="bank-no-citizenship")
    city = await town.by_id(session, entry.city_id)
    if city is None:  # pragma: no cover -- citizenship into nowhere is a bug
        raise NotOurs(key="bank-no-citizenship")

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

    #: The city pays out of its own treasury (D-283), and the treasury is a
    #: remainder shared by everything the city does -- so its row is taken for
    #: the whole read-then-insert. The same lock the treasury's own borrowing
    #: takes (`works_city.credit`), and taken after the citizen's row: one
    #: direction, person -> citizenship -> city, and nothing deadlocks.
    await session.execute(select(City.id).where(City.id == city.id).with_for_update())
    treasury = await town.treasury(session, city)
    own = await ledger.balance(session, treasury.id)
    shortfall = max(0, total - own)
    if shortfall > 0:
        #: Short, so the city borrows the difference from the capital: on its
        #: line, at the key rate, no margin (D-248). The capital does not
        #: borrow from itself -- its treasury fills by emission on the holders'
        #: signatures (D-270) -- so an empty capital simply lends nothing.
        _, _, free = await city_line(session, constants, city, now=moment)
        if city.capital or shortfall > free:
            raise TooMuch(
                key="bank-city-cannot-fund",
                city=city.name,
                own=money_str(own),
                free=money_str(0 if city.capital else max(0, free)),
            )
        await lend_to_city(session, constants, city, shortfall, now=moment, why=who.name)

    margin = city_margin(constants, catalog, city)
    rate_value = await key_rate(session, constants) + margin

    account = await ledger.account_for(session, AccountKind.IDENTITY, who.id)
    await ledger.transfer(
        session,
        PostingReason.LOAN,
        debit=treasury.id,
        credit=account.id,
        amount=total,
        memo={"loan": city.name, "to": who.name},
    )

    loan = Loan(
        identity_id=who.id,
        principal=total,
        outstanding=total,
        rate=rate_value,
        city_id=city.id,
        margin=margin,
        #: Nothing is printed under a citizen's loan any more (D-283): the city
        #: hands over money it already has, and whatever had to be made was
        #: made one level up, under the city's own loan, and is written there.
        printed=0,
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
        printed=0,
        city=city.name,
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
    earns_history: bool = True,
    now: datetime | None = None,
) -> int:
    """Repay debt. Money goes **to the reserve**, not into circulation (D-087).

    **Anyone** may pay (D-063, D-168): a third party may settle for the debtor
    -- and a city from its treasury too (`from_account`). The engine does not
    ask why: money accepted, debt reduced.
    """
    moment = now or datetime.now(UTC)
    await _locked(session, loan)
    if loan.state is not LoanState.OPEN:
        raise NothingToRepay(key="bank-loan-closed")
    await accrue(session, constants, loan, now=moment)

    account = from_account or await ledger.account_for(session, AccountKind.IDENTITY, who.id)
    #: A payer who is also the lender writes the debt down instead of moving
    #: money (D-283), so their balance bounds nothing: a city that lent its
    #: whole treasury out is exactly the city whose convict is working the
    #: debt off, and asking it for cash would close that door on the world
    #: D-283 makes ordinary.
    lender = await _lender_of(session, loan)
    have = (
        loan.outstanding
        if lender is not None and lender.id == account.id
        else await ledger.balance(session, account.id)
    )
    wants = loan.outstanding if amount is None else money(amount)
    payment = min(wants, loan.outstanding, have)
    if payment <= 0:
        raise NothingToRepay(key="bank-nothing-to-pay-with")

    await _settle(session, loan, account, payment, earns_history=earns_history)
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


async def _locked(session: AsyncSession, loan: Loan) -> None:
    """Take the loan's row under this transaction and re-read it.

    Outstanding, accrued and paid are money (CLAUDE.md): they are changed by
    read-modify-write, so without the row two payers both see the same debt
    and both settle it in full -- the ledger moves twice the money, and since
    D-280 the doubled `interest_paid` also buys a limit that was never earned.
    """
    await session.refresh(loan, with_for_update=True)


async def _lender_of(session: AsyncSession, loan: Loan):
    """Whose money is in this loan: the city's treasury, or the capital's reserve.

    One rule, and both the posting and the two gates that ask "has the payer
    got it" read it from here (D-283): a citizen's loan is the city's money, a
    city's own loan and everything from before that decision is the capital's.
    """
    city = None if loan.city_id is None else await town.by_id(session, loan.city_id)
    if loan.identity_id is not None and city is not None:
        return await town.treasury(session, city)
    return await reserve_account(session)


async def _owed_to_city(session: AsyncSession, city, debtor_identity_id: uuid.UUID) -> bool:
    """Whether this debtor owes **this** city -- then a work-off costs it nothing."""
    found = (
        await session.execute(
            select(Loan.id)
            .where(
                Loan.identity_id == debtor_identity_id,
                Loan.city_id == city.id,
                Loan.state == LoanState.OPEN,
            )
            .limit(1)
        )
    ).first()
    return found is not None


async def _settle(
    session: AsyncSession, loan: Loan, account, payment: int, *, earns_history: bool = True
) -> None:
    """Post a payment: interest ahead of principal, and all of it to the lender.

    The banking order stays, because without it the interest cannot be told
    from the principal and "system income" stops being measurable (D-171). What
    went with D-283 is the split: a payment is no longer cut into the city's
    margin and the capital's key share, because the two are no longer two
    lenders in one loan. Whoever lent is paid -- the city's treasury on a
    citizen's loan, the capital's reserve on a city's own.

    Payer and lender can be the same account: the treasury settling the loan it
    issued itself, which is how a prisoner's work-off lands (D-174). Then no
    money moves at all and the debt is simply written down -- the city has
    already been paid, in ore.

    `earns_history` is what tells that write-down from a gift. A credit limit
    grows on interest that left for good (D-280), so a debt the city simply
    forgives its own citizen must not buy them one: otherwise a ruler raises
    somebody's limit with a click and it costs the treasury nothing at all.
    The convict's ore is another matter -- it was paid, in kind.
    """

    interest = min(payment, max(0, loan.interest_accrued - loan.interest_paid))
    lender = await _lender_of(session, loan)

    if payment > 0 and lender.id != account.id:
        await ledger.transfer(
            session,
            PostingReason.LOAN_REPAYMENT,
            debit=account.id,
            credit=lender.id,
            amount=payment,
            memo={"погашение": str(loan.id)},
        )
    if earns_history:
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

    #: Every row this pass will settle, taken at once and in `LOAN_ORDER`. One
    #: at a time deadlocks on a debtor with two loans: the pass holds the first
    #: loan and their account and walks on to the second, while the debtor's
    #: own payment holds that second loan and waits for the same account.
    #: Postgres then kills one of them, and what reaches the player is a raw
    #: deadlock instead of a refusal.
    #:
    #: Overdue is asked in SQL rather than in the loop: a pass that locked
    #: every open loan in the world would hold up payment for debtors who owe
    #: nothing overdue at all, for as long as the whole walk takes.
    since = moment - timedelta(days=constants[R.DEBT_GRACE_PERIOD])
    loans = (
        (
            await session.execute(
                select(Loan)
                #: A treasury loan (D-248) has no identity to withhold
                #: from: a city is collected from by its own pass, out of its
                #: takings (D-285). Left out here, so the two never meet on a
                #: row and nothing waits on the capital's.
                .where(
                    Loan.state == LoanState.OPEN,
                    Loan.identity_id.is_not(None),
                    Loan.serviced_at < since,
                )
                .order_by(LOAN_ORDER)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for loan in loans:
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

    Base from the vault, plus a share of sales turnover, plus a multiple of the
    interest paid, times trust and record. Labour, not the calendar: time in
    game is the cheapest thing to farm (D-173).

    History is measured by **servicing**, not by the round trip (D-280): the
    sum of repaid principal was free to run up -- borrow and repay in the same
    second, interest for zero time is zero, and the limit lifted itself by its
    own bootstraps. Interest is paid money, and it is only paid by whoever
    carried the debt through real time.

    The parts are named rather than worded (D-251 wave IV): the same list is
    read by the bank window and quoted by the refusal of too large a loan, and
    both must say it in the language of whoever is looking.
    """
    moment = now or datetime.now(UTC)
    base_ = money(constants[R.BANK_UNSECURED_LIMIT])
    turnover = await personal_turnover(session, constants, identity_id, now=moment)
    serviced = await interest_paid_total(session, identity_id)
    limit_ = (
        base_
        + int(turnover * constants[R.CREDIT_TURNOVER_SHARE] / PERCENT)
        + int(serviced * constants[R.CREDIT_INTEREST_SHARE] / PERCENT)
    )
    #: Money travels as a formatted string (D-190): the sentence puts it in
    #: as it was written, it does not write it itself.
    reasons = [
        Says("bank-why-limit-base", {"money": money_str(base_)}),
        Says(
            "bank-why-limit-turnover",
            {"money": money_str(turnover), "days": constants[R.CREDIT_WINDOW]},
        ),
        Says("bank-why-limit-interest", {"money": money_str(serviced)}),
    ]

    #: The record is a multiplier, not a base: a bonus for a history without
    #: overdue. It opens on the first interest paid, not on the first loan
    #: closed (D-280): a closure costing nothing bought the multiplier too.
    loans = await loans_of(session, identity_id)
    no_overdue = not any(overdue(constants, loan, moment) for loan in loans)
    if serviced > 0 and no_overdue:
        limit_ = int(limit_ * (1 + constants[R.CREDIT_NO_OVERDUE_BONUS] / PERCENT))
        reasons.append(Says("bank-why-limit-no-overdue"))

    faith = await trust(session, constants, identity_id)
    if faith < 1:
        limit_ = int(limit_ * faith)
        reasons.append(Says("bank-why-limit-trust", {"trust": faith * PERCENT}))
    return limit_, reasons


# --- the city as a debtor (D-285) --------------------------------------------


async def collect_from_cities(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> int:
    """The capital withholds a share of a debtor city's income (D-285). Returns the total.

    A city that does not service what it borrowed pays with its takings: the
    capital keeps `bank.income_withheld_share` of everything that came into the
    treasury since the debt was last serviced. A share of the **income**, not a
    sweep of the balance -- a city whose remainder is swept simply keeps the
    remainder at zero and never pays; a city whose takings are halved pays
    while it lives, and lives while it pays.

    Counted from the journal rather than intercepted at each till, and that is
    the design and not a shortcut: money reaches a treasury from a dozen places
    (duty, tax, works, land, fines) and a thirteenth will be added by somebody
    who never reads this file. The journal has them all, and it has them after
    the fact -- so a city that spent its takings before the pass still owes the
    share of what it took.

    It runs beside the citizens' collection and takes rows the same way -- the
    loan first, its accounts after -- so the two passes and any payment made by
    hand meet in one order and never cross.
    """
    moment = now or datetime.now(UTC)
    share = constants[R.BANK_INCOME_WITHHELD_SHARE] / PERCENT
    withheld = 0

    since = moment - timedelta(days=constants[R.DEBT_GRACE_PERIOD])
    loans = (
        (
            await session.execute(
                select(Loan)
                .where(
                    Loan.state == LoanState.OPEN,
                    Loan.identity_id.is_(None),
                    Loan.serviced_at < since,
                )
                .order_by(LOAN_ORDER)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    #: Grouped by city, because the income is the treasury's and the watermark
    #: sits on the loans: asked once per loan, the same takings would be
    #: charged over and over -- a city with two overdue loans would lose the
    #: whole of its income instead of half, which is exactly what the share
    #: exists to prevent. One budget per city, split between its debts in the
    #: order they are locked in.
    owed_by_city: dict[uuid.UUID, list[Loan]] = {}
    for loan in loans:
        owed_by_city.setdefault(loan.city_id, []).append(loan)

    for city_id, owed in owed_by_city.items():
        city = await town.by_id(session, city_id)
        if city is None:  # pragma: no cover -- a loan of a city that is gone
            continue
        treasury = await town.treasury(session, city)
        #: From the oldest unserviced debt: everything the city took since then
        #: is income this pass has not yet charged for.
        watermark = min(loan.serviced_at for loan in owed)
        income, last_at = await _income_since(session, treasury.id, watermark)
        budget = int(income * share)
        if budget <= 0:
            continue

        collected = 0
        for loan in owed:
            room = budget - collected
            if room <= 0:
                break
            await accrue(session, constants, loan, now=moment)
            payment = min(room, await ledger.balance(session, treasury.id), loan.outstanding)
            if payment <= 0:
                continue

            await _settle(session, loan, treasury, payment)
            if loan.outstanding <= 0:
                loan.state = LoanState.REPAID
                loan.repaid_at = moment
            collected += payment
            await events.record(
                session,
                EventKind.CITY_DEBT_WITHHELD,
                node_id=city.node_id,
                city_id=str(city.id),
                loan_id=str(loan.id),
                amount=payment,
                left=loan.outstanding,
            )
        withheld += collected

        #: The clock moves only when the share was taken whole. Short of that
        #: the city took money the capital has not had its half of -- because
        #: the treasury was already spent -- and the claim waits for the next
        #: takings rather than being forgiven (D-285). It moves to the last
        #: income actually counted, not to the tick's nominal hour: the step
        #: runs minutes after that hour, and anything arriving in between would
        #: otherwise be charged twice or not at all.
        if collected >= budget and last_at is not None:
            for loan in owed:
                loan.serviced_at = last_at
    await session.flush()
    return withheld


async def _income_since(
    session: AsyncSession, account_id: uuid.UUID, since: datetime
) -> tuple[int, datetime | None]:
    """What the city **took** after that moment, and when it last took it.

    Credits only, less what it borrowed: a loan paid into the treasury is not
    income, and withholding a share of it would mean the capital handing a city
    a hundred and taking fifty of it straight back -- the city would end up
    owing a hundred for fifty, which is not a debt but a trick. Everything else
    counts: duty, tax, works, land, fines, and whatever is added to that list
    after this line is written.

    The moment comes back with the sum because it is the next watermark: the
    journal's own clock, so nothing falls between two passes and nothing is
    charged by both.
    """
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(LedgerEntry.amount), 0),
                func.max(LedgerTransaction.at),
            )
            .select_from(LedgerEntry)
            .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
            .where(
                LedgerEntry.account_id == account_id,
                LedgerEntry.amount > 0,
                LedgerTransaction.at > since,
                LedgerTransaction.reason != PostingReason.LOAN,
            )
        )
    ).one()
    return int(row[0] or 0), row[1]


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

    The circle closes inside the city now (D-174, D-283): ore to the city, and
    the debt it is set against is the city's own, so nothing is transferred --
    the claim is written down and the city is paid in ore. Against a loan of
    the capital's -- a city's own borrowing, or a citizen's loan from before
    D-283 -- the treasury pays as it always did, and the money returns to the
    reserve. That is the one case an empty treasury refuses, because it is the
    one case money has to move; against its own claim a city writes the debt
    down however poor it is. Returns how much could be credited.
    """

    moment = now or datetime.now(UTC)
    treasury = await town.treasury(session, city)
    #: Money is only needed when money moves. Against the city's own loan the
    #: ore is the payment and the claim is simply written down (D-283, D-174),
    #: and a treasury emptied by lending is the commonest state there is now --
    #: gating on its balance would mean the poorer the city, the less it may
    #: take from the mine that was built to make it richer.
    if await ledger.balance(session, treasury.id) < cost and not await _owed_to_city(
        session, city, debtor_identity_id
    ):
        return 0

    debtor = await session.get(Identity, debtor_identity_id)
    #: The debtor's rows, taken at once and in `LOAN_ORDER` -- the same order
    #: the daily collection takes them in. Locking them one by one in the order
    #: they were taken is the other half of a deadlock: `Loan.id` is a random
    #: uuid, so "oldest first" and "by id" disagree about half the time, and
    #: the collection runs in the worker while this runs in a player's command.
    held = (
        (
            await session.execute(
                select(Loan)
                .where(Loan.identity_id == debtor_identity_id, Loan.state == LoanState.OPEN)
                .order_by(LOAN_ORDER)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    #: Locked by id, paid oldest first: the order of the queue is a rule of the
    #: bank (D-174), the order of locking is a rule of the database.
    credited = 0
    remainder = cost
    for loan in sorted(held, key=lambda one: one.taken_at):
        if remainder <= 0:
            break
        payment = min(remainder, loan.outstanding)
        if payment <= 0:
            continue
        #: What was actually paid, not what was meant to be: under the lock the
        #: two agree, but `repay` is the one that knows.
        paid = await repay(
            session,
            constants,
            debtor,
            loan,
            payment / MONEY_SCALE,
            from_account=treasury,
            now=moment,
        )
        credited += paid
        remainder -= paid
    if credited > 0:
        await events.record(
            session,
            EventKind.PRISON_WORKOFF,
            actor_identity_id=debtor_identity_id,
            city_id=str(city.id),
            amount=credited,
        )
    return credited
