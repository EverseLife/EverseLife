# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The bank tests' shared fixtures: a borrower with a wallet, the money mass,
an account balance, a market deal, a city with turnover, and the debtor two
passes collide over (`_debtor_of_two_overdue_loans`). Used by the bank files
(`test_bank*.py`) and by the family's races (`test_races_bank.py`); not
collected by pytest.

A borrower here **is a citizen of a city with turnover**, and that is not
convenience but the rule (D-281): only a city lends, only to its own, and only
against its line with the capital -- so an identity belonging nowhere is not a
borrower at all, it is somebody the bank has no answer for.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import ledger, world
from src.models.ledger import AccountKind, PostingReason
from src.units import money


async def _enrol(session: AsyncSession, who, *, turnover: float = 4000):
    """Make this person a citizen of a city whose line with the capital is open.

    Enrolled by hand rather than through the door, because the tests around
    this are about the loan and not about the print: what the door does is
    checked where citizenship is (`test_city_founding`).
    """
    from src.models.city import Citizen

    city = await _city_with_turnover(session, turnover=turnover)
    session.add(Citizen(identity_id=who.id, city_id=city.id))
    await session.flush()
    return city


async def _home(session: AsyncSession, who):
    """The city this person belongs to -- the one that lends to them (D-281)."""
    from src.engine import city as town

    entry = await town.citizenship(session, who.id)
    return None if entry is None else await town.by_id(session, entry.city_id)


async def _borrower(session: AsyncSession, *, funds: float = 0, turnover: float = 4000):
    """A person the bank can serve: a citizen of a city whose line is open."""
    identity = await world.create_identity(session, f"Заёмщик-{uuid.uuid4().hex[:6]}")
    await _enrol(session, identity, turnover=turnover)
    if funds:
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=account.id,
            amount=money(funds),
            memo={},
        )
    return identity


async def _debtor_of_two_overdue_loans(session: AsyncSession, constants, catalog=None):
    """A debtor the bank's two passes can collide over, the city that lent, and
    what the two loans come to.

    Commits before returning: an open transaction of the fixture's own holds
    the rows the race is about to reach for.

    The daily collection runs in the worker and the prison's work-off in a
    player's command, and one debtor with two loans is where they meet. What
    the races here check is that both take the rows in `bank.loan.LOAN_ORDER`
    (`test_races_bank.py`), and the shape the check needs is this one -- so it
    is built once, and not twice with a difference nobody meant.

    Two things about it are the check and not the scenery. The **older loan
    carries the larger id**, so "by id" and "oldest first" name different rows
    first: the ABBA the passes are guarded against cannot happen at all while
    the two possible orders agree. And the debtor's **purse is nothing but the
    loans themselves**: the collection withholds a share of the balance
    (`debt.workoff_rate`), and out of a fat purse that one share settles both
    loans outright -- the work-off then finds nothing open, locks no row, never
    reaches `repay`, and the race is run at a table set for one. That is how
    this family's deadlock test passed for a fortnight without ever letting the
    two passes overlap, so the share is measured here rather than assumed.
    """
    from src.constants import current_catalog
    from src.constants import registry as R
    from src.engine import bank
    from src.engine import city as town
    from src.units import PERCENT

    who = await _borrower(session, turnover=100_000)
    city = await _home(session, who)
    assert city is not None
    #: The city lends out of its own treasury and is paid back into it (D-283),
    #: and the prisoner works off a debt of the city whose citizen they are
    #: (D-281, D-174) -- so the treasury is the one purse that must not be empty.
    treasury = await town.treasury(session, city)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=treasury.id, amount=money(1000)
    )
    await bank.reserve_account(session)
    debts = [
        await bank.borrow(session, constants, catalog or current_catalog(), who, 50)
        for _ in range(2)
    ]

    long_ago = datetime.now(UTC) - timedelta(days=constants[R.DEBT_GRACE_PERIOD] + 5)
    for number, debt in enumerate(sorted(debts, key=lambda one: one.id, reverse=True)):
        debt.taken_at = long_ago + timedelta(hours=number)
        debt.accrued_at = debt.taken_at
        debt.serviced_at = debt.taken_at

    #: The withholding shrinks the purse as it walks the loans, so the first
    #: one is the one in danger of being closed by it; if that one survives,
    #: both do, and whoever comes second finds two open rows to reach for.
    account = await ledger.account_for(session, AccountKind.IDENTITY, who.id)
    purse = await ledger.balance(session, account.id)
    withholding = int(purse * constants[R.DEBT_WORKOFF_RATE] / PERCENT)
    assert 0 < withholding < min(one.outstanding for one in debts), (
        "удержание должно оставить оба займа открытыми, иначе второму проходу нечего запирать"
    )
    #: A third party at a table set for two: both passes would wait on these
    #: rows for as long as the test lives.
    await session.commit()
    #: The whole debt goes back with them, so that a race needing to settle it
    #: can measure what it brings against it instead of guessing a number.
    return who, city, sum(one.outstanding for one in debts)


async def _trade_on(session: AsyncSession, node, price: float, goods: str = "bread"):
    """One concluded deal on this node: a city's line is a share of the trade
    that happened on its land (D-175), so a test that wants a lender wants this."""
    from src.models.market import Order, OrderSide, Trade
    from src.units import amount as _amount

    seller = await world.create_identity(session, f"Купец-{uuid.uuid4().hex[:6]}")
    order_ = Order(
        node_id=node.id,
        identity_id=seller.id,
        side=OrderSide.SELL,
        type_key=goods,
        tier="common",
        price=money(price),
        amount_total=_amount(1),
        amount_left=0,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(order_)
    await session.flush()
    session.add(
        Trade(
            node_id=node.id,
            sell_order_id=order_.id,
            type_key=goods,
            tier="common",
            price=money(price),
            amount=_amount(1),
        )
    )
    await session.flush()
    return order_


async def _city_with_turnover(
    session: AsyncSession, catalog=None, turnover: float = 4000, goods: str = "bread"
):
    """The city on whose territory the deals happened: the share is computed by them.

    The catalog is asked of the loaded vault when not handed in: a borrower
    needs a city of its own (D-281), and threading a fixture through every
    call that only wants a lender would say nothing about the test.
    """
    from src.constants import current_catalog
    from src.engine import city as town
    from src.models.world import Layer

    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session,
        f"terra.city.{stamp}",
        f"Город-{stamp}",
        area_m2=1,
        layer=Layer.PLANET,
        parent=planet,
    )
    marketplace = await world.create_node(
        session,
        f"terra.city.{stamp}.market",
        "Рынок",
        area_m2=50,
        parent=delegate,
    )
    city = await town.found(session, catalog or current_catalog(), delegate, f"Город-{stamp}")
    marketplace.owner_city_id = city.id
    await _trade_on(session, marketplace, turnover, goods=goods)
    return city


# --- price sensor and sterilisation (D-087, D-169) ---------------------------


async def _deal(session: AsyncSession, goods: str, price: float, qty: float, seller=None):
    """A concluded deal: the price index is computed from them."""
    from src.models.market import Trade
    from src.units import amount as _amount

    node = await world.create_node(
        session, f"terra.mkt.{uuid.uuid4().hex[:8]}", "Рынок", area_m2=10
    )
    if seller is None:
        seller = await world.create_identity(session, f"П-{uuid.uuid4().hex[:6]}")
    from src.models.market import Order, OrderSide

    order_ = Order(
        node_id=node.id,
        identity_id=seller.id,
        side=OrderSide.SELL,
        type_key=goods,
        tier="common",
        price=money(price),
        amount_total=_amount(qty),
        amount_left=0,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(order_)
    await session.flush()
    session.add(
        Trade(
            node_id=node.id,
            sell_order_id=order_.id,
            type_key=goods,
            tier="common",
            price=money(price),
            amount=_amount(qty),
        )
    )
    await session.flush()
