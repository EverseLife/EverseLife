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

from bank_kit import _enrol, _trade_on
from conftest import _slow
from src.constants import current, current_catalog
from src.constants import registry as R
from src.engine import city as town
from src.engine import ledger, world
from src.models.city import Citizen
from src.models.identity import Identity
from src.models.ledger import AccountKind, PostingReason
from src.units import MONEY_SCALE, money


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

    who = await world.create_identity(session, f"Заёмщик-{uuid.uuid4().hex[:6]}")
    #: A borrower is somebody's citizen since D-281 -- only a city lends, and
    #: only to its own -- and this one's city is rich enough that the personal
    #: limit binds first, which is the remainder this test is about.
    await _enrol(session, who)
    #: The reserve is opened beforehand: both sessions would otherwise race to
    #: create it, and a unique violation would hide the race being tested.
    await bank.reserve_account(session)
    await session.commit()
    limit_, _ = await bank.credit_limit(session, constants, who.id)
    assert limit_ > 0

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

    who = await world.create_identity(session, f"Должник-{uuid.uuid4().hex[:6]}")
    #: Only a city lends, and only to its own (D-281).
    await _enrol(session, who)
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

    `bank.debt_to_turnover_cap` bounds how much of its turnover a city may owe
    (D-175). Without the city's row two citizens read the same free room and
    both lie down on it -- the cap is through, and the capital prints against
    turnover that was counted once.
    """
    from src.engine import bank
    from src.engine.bank import line as line_module
    from src.models.bank import Loan
    from src.models.city import Citizen
    from src.models.market import Order, OrderSide, Trade
    from src.models.world import Layer
    from src.units import amount as _amount

    stamp = uuid.uuid4().hex[:8]
    catalog = current_catalog()
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session, f"terra.city.{stamp}", "Город", area_m2=1, layer=Layer.PLANET, parent=planet
    )
    marketplace = await world.create_node(
        session, f"terra.city.{stamp}.market", "Рынок", area_m2=50, parent=delegate
    )
    city = await town.found(session, catalog, delegate, f"Город-{stamp}")
    marketplace.owner_city_id = city.id

    #: One deal makes the city's whole turnover, so the line is exactly known --
    #: and small enough that the line binds before anyone's personal limit does
    #: (`bank.unsecured_limit`), otherwise both borrowers are refused for their
    #: own reasons and the race never happens.
    merchant = await world.create_identity(session, f"Купец-{stamp}")
    order = Order(
        node_id=marketplace.id,
        identity_id=merchant.id,
        side=OrderSide.SELL,
        type_key="bread",
        tier="common",
        price=money(50),
        amount_total=_amount(1),
        amount_left=0,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(order)
    await session.flush()
    session.add(
        Trade(
            node_id=marketplace.id,
            sell_order_id=order.id,
            type_key="bread",
            tier="common",
            price=money(50),
            amount=_amount(1),
        )
    )
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

    permitted, occupied, free = await bank.city_line(session, constants, city)
    assert occupied == 0 and free == permitted > 0
    limit_, _ = await bank.credit_limit(session, constants, borrowers[0])
    assert free < limit_, "линия города должна упереться раньше личного лимита"

    _slow(monkeypatch, line_module, "city_outstanding")

    async def take(identity_id: uuid.UUID) -> None:
        async with factory() as db, db.begin():
            borrower = await db.get(Identity, identity_id)
            assert borrower is not None
            await bank.borrow(db, constants, catalog, borrower, free / MONEY_SCALE)

    outcomes = await asyncio.gather(*(take(one) for one in borrowers), return_exceptions=True)
    #: One is served and one is refused: past the city's line there is nothing
    #: any more -- the direct loan from the capital at the worse rate went with
    #: D-281, so the line is the whole answer. Without the city's row the two
    #: collide in the ledger instead, and one of them dies of a deadlock: the
    #: cap holds by accident, and the player sees a crash instead of a refusal.
    refused = [one for one in outcomes if isinstance(one, Exception)]
    assert len(refused) == 1, f"второму занять нечего: {outcomes}"
    assert isinstance(refused[0], bank.TooMuch), refused[0]

    async with factory() as db:
        on_line = (
            (
                await db.execute(
                    select(Loan).where(Loan.city_id == city.id, Loan.identity_id.in_(borrowers))
                )
            )
            .scalars()
            .all()
        )
        assert on_line, "первый заём лёг на линию города"
        assert sum(one.outstanding for one in on_line) <= permitted, (
            "на линию города легло больше, чем город может занять"
        )


async def test_collection_and_the_prison_do_not_deadlock_on_one_debtor(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two passes reach for the same debtor's loans, and must take them alike.

    The daily collection runs in the worker, the prison's work-off in a
    player's command, and a debtor with two loans is where they meet. Locking
    one loan at a time in two different orders is a textbook ABBA: `Loan.id` is
    a random uuid, so any business order -- oldest first, for instance --
    disagrees with it about half the time, and Postgres kills one side with a
    raw deadlock. Here the two orders are made to disagree on purpose.
    """
    from src.engine import bank
    from src.engine.bank import loan as loan_module
    from src.models.bank import Loan
    from src.models.world import Layer

    stamp = uuid.uuid4().hex[:8]
    catalog = current_catalog()
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session, f"terra.city.{stamp}", "Город", area_m2=1, layer=Layer.PLANET, parent=planet
    )
    city = await town.found(session, catalog, delegate, f"Город-{stamp}")
    treasury = await town.treasury(session, city)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=treasury.id, amount=money(1000)
    )

    who = await world.create_identity(session, f"Каторжник-{stamp}")
    #: The city lends to its own and only within its line (D-281), so the
    #: convict belongs to this city and this city has seen some trade.
    session.add(Citizen(identity_id=who.id, city_id=city.id))
    await _trade_on(session, delegate, 1000)
    account = await ledger.account_for(session, AccountKind.IDENTITY, who.id)
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=account.id, amount=money(300)
    )
    await bank.reserve_account(session)
    debts = [await bank.borrow(session, constants, catalog, who, 50) for _ in range(2)]

    #: The two orders are made to disagree: the loan with the larger id is the
    #: older one, so "oldest first" and "by id" name different rows first.
    long_ago = datetime.now(UTC) - timedelta(days=constants[R.DEBT_GRACE_PERIOD] + 5)
    for number, debt in enumerate(sorted(debts, key=lambda one: one.id, reverse=True)):
        debt.taken_at = long_ago + timedelta(hours=number)
        debt.accrued_at = debt.taken_at
        debt.serviced_at = debt.taken_at
    await session.commit()

    _slow(monkeypatch, loan_module, "_locked")

    async def withhold() -> int:
        async with factory() as db, db.begin():
            return await bank.collect(db, constants)

    async def workoff() -> int:
        async with factory() as db, db.begin():
            own = await town.by_id(db, city.id)
            assert own is not None
            return await bank.prison_credit(db, constants, own, who.id, money(50))

    outcomes = await asyncio.gather(withhold(), workoff(), return_exceptions=True)
    assert not [one for one in outcomes if isinstance(one, Exception)], outcomes

    async with factory() as db:
        after = (await db.execute(select(Loan).where(Loan.identity_id == who.id))).scalars().all()
        assert sum(one.interest_paid for one in after) <= sum(
            one.interest_accrued for one in after
        ), "уплаченный процент не может обогнать начисленный: он покупает лимит"
