"""Проводки: единственный способ, которым в игре двигаются деньги.

Правило одно и оно жёсткое: **сумма проводок операции равна нулю**. Деньги
переходят, а не появляются (инвариант И2, D-127). Функция `post` не умеет
списать, не зачислив, — просто потому что не принимает такой аргумент.

Прирост денежной массы возможен только через счёт `genesis`, и это отдельная
операция с отдельным основанием: её видно в журнале, по ней бьёт проверка
инвариантов, и объяснить её обязан человек.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.ledger import (
    AccountKind,
    Currency,
    LedgerAccount,
    LedgerEntry,
    LedgerTransaction,
    PostingReason,
)


class LedgerError(Exception):
    pass


class Unbalanced(LedgerError):
    """Проводки не сходятся в ноль. Это баг движка, а не ситуация в игре."""


class InsufficientFunds(LedgerError):
    """На счёте нет денег. Это как раз ситуация в игре, и она нормальна."""


@dataclass(frozen=True, slots=True)
class Posting:
    """Одна сторона операции. Знак: списание отрицательно."""

    account_id: uuid.UUID
    amount: int


async def account_for(
    session: AsyncSession,
    kind: AccountKind,
    owner_id: uuid.UUID | None,
    currency: Currency = Currency.TK,
) -> LedgerAccount:
    """Найти счёт или завести его. Счёт сам по себе денег не создаёт."""
    stmt = select(LedgerAccount).where(
        LedgerAccount.kind == kind,
        LedgerAccount.owner_id == owner_id,
        LedgerAccount.currency == currency,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing
    account = LedgerAccount(kind=kind, owner_id=owner_id, currency=currency)
    session.add(account)
    await session.flush()
    return account


async def balance(session: AsyncSession, account_id: uuid.UUID) -> int:
    """Баланс — производная от журнала, а не хранимое поле."""
    stmt = select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
        LedgerEntry.account_id == account_id
    )
    return int((await session.execute(stmt)).scalar_one())


async def post(
    session: AsyncSession,
    reason: PostingReason,
    postings: Sequence[Posting],
    *,
    event_id: int | None = None,
    memo: dict | None = None,
    allow_overdraft: bool = False,
) -> LedgerTransaction:
    """Провести операцию целиком.

    `allow_overdraft` существует для долга перед городом: штраф списывается
    даже с пустого счёта и превращается в долг (санкция `fine`). Во всех
    остальных случаях уход в минус — ошибка вызывающего кода.
    """
    if not postings:
        raise Unbalanced("операция без проводок")

    total = sum(p.amount for p in postings)
    if total != 0:
        raise Unbalanced(
            f"проводки не сходятся: сумма {total}, основание {reason}. "
            f"Деньги переходят, а не появляются (И2)"
        )

    if not allow_overdraft:
        await _check_funds(session, postings)

    transaction = LedgerTransaction(reason=reason, event_id=event_id, memo=memo or {})
    session.add(transaction)
    await session.flush()

    for posting in postings:
        session.add(
            LedgerEntry(
                transaction_id=transaction.id,
                account_id=posting.account_id,
                amount=posting.amount,
            )
        )
    await session.flush()
    return transaction


async def transfer(
    session: AsyncSession,
    reason: PostingReason,
    *,
    debit: uuid.UUID,
    credit: uuid.UUID,
    amount: int,
    event_id: int | None = None,
    memo: dict | None = None,
    allow_overdraft: bool = False,
) -> LedgerTransaction:
    """Частый случай: переложить из кармана в карман."""
    if amount <= 0:
        raise LedgerError(f"перевод должен быть положительным, получено {amount}")
    return await post(
        session,
        reason,
        [Posting(debit, -amount), Posting(credit, amount)],
        event_id=event_id,
        memo=memo,
        allow_overdraft=allow_overdraft,
    )


async def _check_funds(session: AsyncSession, postings: Sequence[Posting]) -> None:
    spending: dict[uuid.UUID, int] = {}
    for posting in postings:
        if posting.amount < 0:
            spending[posting.account_id] = spending.get(posting.account_id, 0) + posting.amount

    for account_id, delta in spending.items():
        account = await session.get(LedgerAccount, account_id)
        #: Genesis — единственный счёт, которому позволено быть в минусе:
        #: его отрицательный баланс и есть выпущенная денежная масса.
        if account is not None and account.kind is AccountKind.GENESIS:
            continue
        current = await balance(session, account_id)
        if current + delta < 0:
            raise InsufficientFunds(
                f"на счёте {account_id} {current}, требуется {-delta}"
            )


async def money_supply(session: AsyncSession, currency: Currency = Currency.TK) -> int:
    """Сколько денег в мире.

    Равно минус балансу genesis-счетов. Проверка И2 в телеметрии: величина
    меняется только вместе с явной операцией выпуска.
    """
    stmt = (
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(LedgerAccount, LedgerAccount.id == LedgerEntry.account_id)
        .where(
            LedgerAccount.kind == AccountKind.GENESIS,
            LedgerAccount.currency == currency,
        )
    )
    return -int((await session.execute(stmt)).scalar_one())
