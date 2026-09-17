# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once on one debt, one limit, one city line.

The bank's half of the race family (`test_races*.py`). Money here is not only
the ledger: since D-280 the interest paid is an input of the credit limit, so
a lost update does not merely move money twice -- it buys a limit that was
never earned. And the loan is the one row two passes reach for from two
processes: the daily collection from the worker, the prison's work-off and a
payment from a player's command. That is why every place that locks more than
one loan keeps to `bank.loan.LOAN_ORDER`, and why that is tested here.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bank_kit import _borrower, _city_with_turnover, _debtor_of_two_overdue_loans, _home
from conftest import _hold_the_first, _slow
from src.constants import current, current_catalog
from src.constants import registry as R
from src.engine import city as town
from src.engine import ledger, world
from src.models.identity import Identity
from src.models.ledger import AccountKind, PostingReason
from src.units import MONEY_SCALE, money

#: What the prisoner's ore is worth in the two races below. A work-off that
#: ran out at the first loan would leave the two passes contending over a
#: single row, and one row cannot ABBA -- so it has to cover both, and each
#: race weighs it against the debt rather than trusting the number.
ORE_FOR_BOTH_LOANS = money(200)


async def test_two_loans_of_the_same_room_leave_one_refused(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The free room under the credit limit is a remainder like a purse.

    A loan adds a row instead of changing one, so there is nothing to lock but
    the borrower: without that row two commands read the same room and both
    take it in full, and the world prints money nobody's limit allowed.
    """
    from src.engine import bank
    from src.engine.bank import loan as loan_module
    from src.models.bank import Loan

    #: A borrower belongs to a city (D-281), and its line is left wide: the
    #: brake under test is the personal limit, not the city's.
    who = await _borrower(session, turnover=100_000)
    #: The reserve is opened beforehand: both sessions would otherwise race to
    #: create it, and a unique violation would hide the race being tested.
    await bank.reserve_account(session)
    await session.commit()
    limit_, _ = await bank.credit_limit(session, constants, who.id)
    assert limit_ > 0
    _, _, free = await bank.city_line(session, constants, await _home(session, who))
    assert free > limit_, "линия города не должна упереться раньше личного лимита"

    _slow(monkeypatch, loan_module, "debt_of")

    async def take() -> None:
        async with factory() as db, db.begin():
            borrower = await db.get(Identity, who.id)
            assert borrower is not None
            await bank.borrow(db, constants, current_catalog(), borrower, limit_ / MONEY_SCALE)

    outcomes = await asyncio.gather(*(take() for _ in range(2)), return_exceptions=True)
    refused = [one for one in outcomes if isinstance(one, bank.TooMuch)]
    assert len(refused) == 1, f"второй заём должен упереться в лимит: {outcomes}"

    async with factory() as db:
        taken = (await db.execute(select(Loan).where(Loan.identity_id == who.id))).scalars().all()
        assert sum(one.outstanding for one in taken) == limit_, "долг вышел за лимит"


async def test_two_payments_of_the_same_debt_are_not_counted_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anyone may pay for the debtor (D-063), so two payers meet on one loan.

    Without the loan's row both read the same outstanding and both settle it
    in full: the ledger moves twice the money, and since D-280 the doubled
    `interest_paid` buys a credit limit that was never earned.
    """
    from src.engine import bank
    from src.engine.bank import loan as loan_module
    from src.models.bank import Loan

    who = await _borrower(session)
    account = await ledger.account_for(session, AccountKind.IDENTITY, who.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=account.id, amount=money(500)
    )
    taken_at = datetime.now(UTC) - timedelta(days=constants[R.BANK_YEAR_DAYS])
    debt = await bank.borrow(session, current(), current_catalog(), who, 100, now=taken_at)
    await session.commit()

    _slow(monkeypatch, loan_module, "accrue")

    async def pay() -> int:
        async with factory() as db, db.begin():
            payer = await db.get(Identity, who.id)
            owed = await db.get(Loan, debt.id)
            assert payer is not None and owed is not None
            return await bank.repay(db, constants, payer, owed)

    outcomes = await asyncio.gather(*(pay() for _ in range(2)), return_exceptions=True)
    refused = [one for one in outcomes if isinstance(one, bank.NothingToRepay)]
    assert len(refused) == 1, f"платить дважды по одному займу нечем: {outcomes}"

    async with factory() as db:
        settled = await db.get(Loan, debt.id)
        assert settled is not None
        assert settled.outstanding == 0
        assert settled.interest_paid == settled.interest_accrued, (
            "уплаченный процент не может быть больше начисленного: он покупает лимит"
        )


async def test_two_citizens_do_not_lie_twice_on_the_same_line(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The city's line with the capital is one remainder for all its citizens.

    The line bounds how much a city may owe the capital (D-175, D-285), and
    since D-283 a citizen's loan reaches it through the city's own borrowing:
    an empty treasury sends the city to the capital for every coin. Without the
    city's row two citizens read the same free room and both lie down on it --
    the ceiling is through, and the capital prints against a line counted once.
    """
    from src.engine import bank
    from src.engine.bank import line as line_module
    from src.models.bank import Loan, LoanState
    from src.models.city import Citizen

    stamp = uuid.uuid4().hex[:8]
    catalog = current_catalog()
    #: One deal makes the city's whole turnover, so the line is exactly known --
    #: and small enough that the line binds before anyone's personal limit does
    #: (`bank.unsecured_limit`), otherwise both borrowers are refused for their
    #: own reasons and the race never happens.
    city = await _city_with_turnover(session, catalog, turnover=50)
    borrowers = []
    for number in range(2):
        who = await world.create_identity(session, f"Гражданин-{number}-{stamp}")
        session.add(Citizen(identity_id=who.id, city_id=city.id))
        borrowers.append(who.id)
    #: The reserve and the genesis are opened beforehand: both borrowers print
    #: their shortfall, and two sessions racing to create the same account
    #: collide in the ledger before they ever reach the line -- the cap would
    #: then hold by a deadlock instead of by the lock under test.
    await bank.reserve_account(session)
    await ledger.account_for(session, AccountKind.GENESIS, None)
    await session.commit()

    limit_, _ = await bank.credit_limit(session, constants, borrowers[0])
    #: What is left of the city's line is brought down under the personal limit
    #: -- the line is a lot wider since D-285, and the race is about the line,
    #: not about anybody's own ceiling. The city's treasury stays empty, so each
    #: loan to a citizen goes to the capital for the whole of it (D-283) and
    #: lands on the very line under test.
    _, _, free = await bank.city_line(session, constants, city)
    if free > limit_ // 2:
        await bank.lend_to_city(session, constants, city, free - limit_ // 2, why="тест")
    #: And what that loan brought is spent, because it landed in the treasury:
    #: a city with money in hand pays its citizens out of it and never reaches
    #: the line at all (D-283). Empty, it goes to the capital for every coin,
    #: and the race is back where this test wants it.
    treasury = await town.treasury(session, city)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=treasury.id,
        credit=genesis.id,
        amount=await ledger.balance(session, treasury.id),
    )
    permitted, occupied, free = await bank.city_line(session, constants, city)
    assert 0 < free < limit_, "линия города упирается раньше личного лимита"
    #: And the fixture's own transaction is closed before the race: the loan
    #: above is a write, and an open transaction holding its rows is a third
    #: party at a table set for two -- both borrowers would wait on it for as
    #: long as the test lives.
    await session.commit()

    _slow(monkeypatch, line_module, "city_outstanding")

    async def take(identity_id: uuid.UUID) -> None:
        async with factory() as db, db.begin():
            borrower = await db.get(Identity, identity_id)
            assert borrower is not None
            await bank.borrow(db, constants, catalog, borrower, free / MONEY_SCALE)

    outcomes = await asyncio.gather(*(take(one) for one in borrowers), return_exceptions=True)
    #: One is served and one is told the line is out -- past it there is
    #: nothing any more (D-281). Without the city's row the two collide in the
    #: ledger instead and one dies of a deadlock: the cap then holds by
    #: accident, and what the player sees is a crash and not a refusal.
    refused = [one for one in outcomes if isinstance(one, bank.TooMuch)]
    assert len(refused) == 1, f"второй должен упереться в линию города: {outcomes}"
    assert not [
        one for one in outcomes if isinstance(one, Exception) and not isinstance(one, bank.TooMuch)
    ], outcomes

    async with factory() as db:
        #: What sits on the line is the CITY's own borrowing (D-283): a citizen's
        #: loan is paid out of the treasury and is the city's asset, not its
        #: debt. Counting the citizens' rows here would measure the rule with
        #: the wrong ruler and pass whatever the line did.
        on_line = (
            (
                await db.execute(
                    select(Loan).where(
                        Loan.city_id == city.id,
                        Loan.identity_id.is_(None),
                        Loan.state == LoanState.OPEN,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert on_line, "город занял у столицы под первый же заём гражданина"
        assert sum(one.outstanding for one in on_line) <= permitted, (
            "на линию города легло больше, чем город может занять"
        )


async def test_collection_and_the_prison_do_not_deadlock_on_one_debtor(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The collection takes the debtor's loans first, the work-off comes to them.

    Locking one loan at a time in two different orders is a textbook ABBA:
    `Loan.id` is a random uuid, so any business order -- oldest first, for
    instance -- disagrees with it about half the time, and Postgres kills one
    side with a raw deadlock. `_debtor_of_two_overdue_loans` makes the two
    orders disagree on purpose and leaves both loans open under the
    withholding, so the second pass has two rows to reach for and not one.

    The handshake is `_until_blocked_by` and not a guessed pause: the
    collection holds the rows it locked until the work-off provably waits on
    them, and the helper fails the test outright if nobody ever comes to wait.
    Rewrite the collection to take its loans one at a time and oldest first,
    and this test dies of `DeadlockDetectedError`.
    """
    from src.engine import bank
    from src.engine.bank import loan as loan_module
    from src.models.bank import Loan

    who, city, owed = await _debtor_of_two_overdue_loans(session, constants, current_catalog())
    assert owed < ORE_FOR_BOTH_LOANS, "руды должно хватить на оба займа и на процент по ним"

    #: `accrue` is the first thing either pass does to a loan it has taken, so
    #: by the time it is called the rows are under the lock -- all of them at
    #: once while the order is kept, and only the first of them if it is not.
    #: The hold replaces one name in one module, which is enough only because
    #: `collect` and `repay` both live in `loan.py` and read it from their own
    #: globals: move either into a sibling room of the package and the pause
    #: goes quiet (`conftest._slow` covers a package, this does not).
    held = _hold_the_first(monkeypatch, factory, loan_module, "accrue")

    async def withhold() -> int:
        async with factory() as db, db.begin():
            return await bank.collect(db, constants)

    async def workoff() -> int:
        #: The collection is provably holding the debtor's loans by now. With a
        #: deadline, because the suite has none of its own: a handshake that
        #: never comes must fail the run and not hang it.
        await asyncio.wait_for(held.wait(), timeout=5)
        async with factory() as db, db.begin():
            own = await town.by_id(db, city.id)
            assert own is not None
            return await bank.prison_credit(db, constants, own, who.id, ORE_FOR_BOTH_LOANS)

    race_began = datetime.now(UTC)
    outcomes = await asyncio.gather(withhold(), workoff(), return_exceptions=True)
    assert not [one for one in outcomes if isinstance(one, Exception)], outcomes
    withheld, credited = outcomes
    #: Both passes did their work on the contended rows: a run where either of
    #: these is nothing is a run in which the order of locking was never asked
    #: anything at all.
    assert withheld > 0, "удержание прошло мимо просроченных займов"
    assert credited > 0, "отработка прошла мимо открытых займов"

    async with factory() as db:
        after = (await db.execute(select(Loan).where(Loan.identity_id == who.id))).scalars().all()
        #: `repay` is the only thing in this race that stamps `serviced_at`:
        #: the withholding leaves it alone on purpose, so that the insolvent do
        #: not hang in the grace period forever. A stamp from inside the race is
        #: therefore the work-off's own mark, and both rows carry one -- had the
        #: work-off stopped at the first loan, the two passes would have
        #: contended over a single row, and one row cannot ABBA.
        assert len([one for one in after if one.serviced_at >= race_began]) == 2, (
            "отработка дошла до обоих займов, иначе перекрёстка на двух строках не было"
        )
        assert sum(one.interest_paid for one in after) <= sum(
            one.interest_accrued for one in after
        ), "уплаченный процент не может обогнать начисленный: он покупает лимит"


async def test_the_prison_ahead_of_collection_does_not_deadlock_on_one_debtor(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same two passes, the other way round -- and the other half of the guard.

    Which side arrives first is not a detail here: the pass that gets the rows
    first is the only one that can be holding one of them while it reaches for
    another, so it is the only one whose order the deadlock can catch. With the
    collection ahead, a work-off that went back to locking its loans one at a
    time and oldest first would simply queue up behind it and pass. Hence this
    test, where the work-off is ahead; written that way, it dies of
    `DeadlockDetectedError`.

    The collection is not expected to withhold anything here: the work-off
    services both loans under it, and what the collection re-reads once the
    rows come free is a debt serviced this second and overdue no longer. That
    it reached for those rows at all is what `_until_blocked_by` establishes --
    it fails the test if nobody ever comes to wait on the held rows.
    """
    from src.engine import bank
    from src.engine.bank import loan as loan_module
    from src.models.bank import Loan

    who, city, owed = await _debtor_of_two_overdue_loans(session, constants, current_catalog())
    assert owed < ORE_FOR_BOTH_LOANS, "руды должно хватить на оба займа и на процент по ним"

    held = _hold_the_first(monkeypatch, factory, loan_module, "accrue")

    async def workoff() -> int:
        async with factory() as db, db.begin():
            own = await town.by_id(db, city.id)
            assert own is not None
            return await bank.prison_credit(db, constants, own, who.id, ORE_FOR_BOTH_LOANS)

    async def withhold() -> int:
        #: The work-off is provably holding the debtor's loans by now, and the
        #: wait has a deadline for the same reason as in the race above.
        await asyncio.wait_for(held.wait(), timeout=5)
        async with factory() as db, db.begin():
            return await bank.collect(db, constants)

    race_began = datetime.now(UTC)
    outcomes = await asyncio.gather(workoff(), withhold(), return_exceptions=True)
    assert not [one for one in outcomes if isinstance(one, Exception)], outcomes
    credited = outcomes[0]
    assert credited > 0, "отработка прошла мимо открытых займов"

    async with factory() as db:
        after = (await db.execute(select(Loan).where(Loan.identity_id == who.id))).scalars().all()
        #: The work-off's own mark on both rows again, read as in the race
        #: above: `serviced_at` from inside the race can only be `repay`'s.
        assert len([one for one in after if one.serviced_at >= race_began]) == 2, (
            "отработка дошла до обоих займов, иначе перекрёстка на двух строках не было"
        )
        assert sum(one.interest_paid for one in after) <= sum(
            one.interest_accrued for one in after
        ), "уплаченный процент не может обогнать начисленный: он покупает лимит"
