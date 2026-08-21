# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Money moves, it does not appear (invariant I2, D-127).

These checks are not about SQLAlchemy. They are about **an unbalanced operation
being impossible to post**: neither through engine code nor around it by a query.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import ledger
from src.models.ledger import AccountKind, Currency, LedgerEntry, PostingReason
from src.units import money


async def _funded(session: AsyncSession, amount: int) -> tuple:
    """An identity with money. The money is issued by an explicit operation from the genesis
    account."""
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    wallet = await ledger.account_for(session, AccountKind.IDENTITY, uuid.uuid4())
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=wallet.id, amount=amount
    )
    return genesis, wallet


async def test_transfer_preserves_sum(session: AsyncSession) -> None:
    _, seller = await _funded(session, money(100))
    buyer = await ledger.account_for(session, AccountKind.IDENTITY, uuid.uuid4())
    await ledger.transfer(
        session, PostingReason.GENESIS,
        debit=(await ledger.account_for(session, AccountKind.GENESIS, None)).id,
        credit=buyer.id, amount=money(50),
    )

    await ledger.transfer(
        session, PostingReason.TRADE, debit=buyer.id, credit=seller.id, amount=money(30)
    )
    await session.commit()

    assert await ledger.balance(session, buyer.id) == money(20)
    assert await ledger.balance(session, seller.id) == money(130)


async def test_seller_gets_exactly_minus_tax(session: AsyncSession) -> None:
    """D-127: the buyer sees the price -- it is the price. The seller pays the tax."""
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    buyer = await ledger.account_for(session, AccountKind.IDENTITY, uuid.uuid4())
    seller = await ledger.account_for(session, AccountKind.IDENTITY, uuid.uuid4())
    treasury = await ledger.account_for(session, AccountKind.CITY_TREASURY, uuid.uuid4())
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=buyer.id, amount=money(100)
    )

    price, tax = money(40), money(1.2)
    await ledger.post(
        session,
        PostingReason.TRADE,
        [
            ledger.Posting(buyer.id, -price),
            ledger.Posting(seller.id, price - tax),
            ledger.Posting(treasury.id, tax),
        ],
    )
    await session.commit()

    assert await ledger.balance(session, buyer.id) == money(60)
    assert await ledger.balance(session, seller.id) == price - tax
    assert await ledger.balance(session, treasury.id) == tax
    #: The money supply did not change: tax is a transfer, not burning.
    assert await ledger.money_supply(session) == money(100)


async def test_unbalanced_operation_rejected_by_engine(session: AsyncSession) -> None:
    _, wallet = await _funded(session, money(10))
    other = await ledger.account_for(session, AccountKind.IDENTITY, uuid.uuid4())

    with pytest.raises(ledger.Unbalanced):
        await ledger.post(
            session,
            PostingReason.TRADE,
            [ledger.Posting(wallet.id, -money(5)), ledger.Posting(other.id, money(7))],
        )


async def test_unbalanced_operation_rejected_by_database(session: AsyncSession) -> None:
    """The main check: the rule holds even if the engine was bypassed by a query."""
    _, wallet = await _funded(session, money(10))
    await session.commit()

    transaction = await ledger.post(
        session,
        PostingReason.TRADE,
        [
            ledger.Posting(wallet.id, -money(1)),
            ledger.Posting(
                (await ledger.account_for(session, AccountKind.ESCROW, uuid.uuid4())).id,
                money(1),
            ),
        ],
    )
    #: We add half a posting past the engine -- the way a bug in code or a
    #: hand edit would do it.
    session.add(LedgerEntry(transaction_id=transaction.id, account_id=wallet.id, amount=money(5)))

    with pytest.raises(DBAPIError, match="не сходятся"):
        await session.commit()


async def test_cannot_spend_what_you_do_not_have(session: AsyncSession) -> None:
    _, wallet = await _funded(session, money(10))
    other = await ledger.account_for(session, AccountKind.IDENTITY, uuid.uuid4())

    with pytest.raises(ledger.InsufficientFunds):
        await ledger.transfer(
            session, PostingReason.TRADE, debit=wallet.id, credit=other.id, amount=money(11)
        )


async def test_fine_pushes_account_into_debt_deliberately(session: AsyncSession) -> None:
    """The `fine` sanction: a write-off with a shortfall turns into debt to the city."""
    _, wallet = await _funded(session, money(5))
    treasury = await ledger.account_for(session, AccountKind.CITY_TREASURY, uuid.uuid4())

    await ledger.transfer(
        session,
        PostingReason.FINE,
        debit=wallet.id,
        credit=treasury.id,
        amount=money(20),
        allow_overdraft=True,
    )
    await session.commit()
    assert await ledger.balance(session, wallet.id) == money(-15)


async def test_money_supply_grows_only_via_genesis(session: AsyncSession) -> None:
    assert await ledger.money_supply(session) == 0
    _, wallet = await _funded(session, money(100))
    other = await ledger.account_for(session, AccountKind.IDENTITY, uuid.uuid4())
    await ledger.transfer(
        session, PostingReason.TRADE, debit=wallet.id, credit=other.id, amount=money(40)
    )
    await session.commit()
    #: However much you move it around -- the supply is the same.
    assert await ledger.money_supply(session) == money(100)


async def test_posting_journal_immutable(session: AsyncSession) -> None:
    _, wallet = await _funded(session, money(10))
    await session.commit()

    with pytest.raises(DBAPIError, match="только для добавления"):
        await session.execute(
            LedgerEntry.__table__.update().where(LedgerEntry.account_id == wallet.id).values(
                amount=money(999)
            )
        )
        await session.commit()
    await session.rollback()


async def test_account_reused_not_multiplied(session: AsyncSession) -> None:
    owner = uuid.uuid4()
    first = await ledger.account_for(session, AccountKind.IDENTITY, owner, Currency.TK)
    second = await ledger.account_for(session, AccountKind.IDENTITY, owner, Currency.TK)
    assert first.id == second.id
