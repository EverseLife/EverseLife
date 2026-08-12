"""Деньги переходят, а не появляются (инвариант И2, D-127).

Эти проверки — не про SQLAlchemy. Они про то, что **несходящуюся операцию
невозможно провести**: ни через код движка, ни в обход него запросом.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from octoverse.engine import ledger
from octoverse.models.ledger import AccountKind, Currency, LedgerEntry, PostingReason
from octoverse.units import money


async def _funded(session: AsyncSession, amount: int) -> tuple:
    """Личность с деньгами. Деньги выпущены явной операцией с genesis-счёта."""
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    wallet = await ledger.account_for(session, AccountKind.IDENTITY, uuid.uuid4())
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=wallet.id, amount=amount
    )
    return genesis, wallet


async def test_перевод_сохраняет_сумму(session: AsyncSession) -> None:
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


async def test_продавец_получает_ровно_минус_налог(session: AsyncSession) -> None:
    """D-127: покупатель видит цену — она и есть цена. Налог платит продавец."""
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
    #: Денежная масса не изменилась: налог — переход, а не сжигание.
    assert await ledger.money_supply(session) == money(100)


async def test_несходящаяся_операция_отвергается_движком(session: AsyncSession) -> None:
    _, wallet = await _funded(session, money(10))
    other = await ledger.account_for(session, AccountKind.IDENTITY, uuid.uuid4())

    with pytest.raises(ledger.Unbalanced):
        await ledger.post(
            session,
            PostingReason.TRADE,
            [ledger.Posting(wallet.id, -money(5)), ledger.Posting(other.id, money(7))],
        )


async def test_несходящаяся_операция_отвергается_базой(session: AsyncSession) -> None:
    """Главная проверка: правило держится, даже если движок обошли запросом."""
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
    #: Добавляем половину проводки мимо движка — так, как это сделала бы
    #: ошибка в коде или правка руками.
    session.add(LedgerEntry(transaction_id=transaction.id, account_id=wallet.id, amount=money(5)))

    with pytest.raises(DBAPIError, match="не сходятся"):
        await session.commit()


async def test_нельзя_потратить_чего_нет(session: AsyncSession) -> None:
    _, wallet = await _funded(session, money(10))
    other = await ledger.account_for(session, AccountKind.IDENTITY, uuid.uuid4())

    with pytest.raises(ledger.InsufficientFunds):
        await ledger.transfer(
            session, PostingReason.TRADE, debit=wallet.id, credit=other.id, amount=money(11)
        )


async def test_штраф_уводит_счёт_в_долг_осознанно(session: AsyncSession) -> None:
    """Санкция `fine`: списание при нехватке превращается в долг перед городом."""
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


async def test_денежная_масса_растёт_только_через_genesis(session: AsyncSession) -> None:
    assert await ledger.money_supply(session) == 0
    _, wallet = await _funded(session, money(100))
    other = await ledger.account_for(session, AccountKind.IDENTITY, uuid.uuid4())
    await ledger.transfer(
        session, PostingReason.TRADE, debit=wallet.id, credit=other.id, amount=money(40)
    )
    await session.commit()
    #: Сколько ни перекладывай — масса та же.
    assert await ledger.money_supply(session) == money(100)


async def test_журнал_проводок_неизменяем(session: AsyncSession) -> None:
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


async def test_счёт_переиспользуется_а_не_плодится(session: AsyncSession) -> None:
    owner = uuid.uuid4()
    first = await ledger.account_for(session, AccountKind.IDENTITY, owner, Currency.TK)
    second = await ledger.account_for(session, AccountKind.IDENTITY, owner, Currency.TK)
    assert first.id == second.id
