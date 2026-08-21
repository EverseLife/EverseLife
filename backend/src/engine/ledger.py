# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Postings: the only way money moves in the game.

One rule, and it is strict: **the postings of an operation sum to zero**.
Money moves, it does not appear (invariant I2, D-127). The `post` function
cannot debit without crediting -- simply because it accepts no such argument.

Growth of the money supply is possible only through the `genesis` account,
and that is a separate operation with a separate ground: it is visible in the
journal, the invariant check flags it, and a human must explain it.
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
    """The postings do not sum to zero. This is an engine bug, not an in-game situation."""


class InsufficientFunds(LedgerError):
    """No money on the account. This is exactly an in-game situation, and it is normal."""


@dataclass(frozen=True, slots=True)
class Posting:
    """One side of an operation. Sign: a debit is negative."""

    account_id: uuid.UUID
    amount: int


async def account_for(
    session: AsyncSession,
    kind: AccountKind,
    owner_id: uuid.UUID | None,
    currency: Currency = Currency.TK,
) -> LedgerAccount:
    """Find an account or create it. An account by itself creates no money."""
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
    """The balance is derived from the journal, not a stored field."""
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
    """Post a whole operation.

    `allow_overdraft` exists for debt to the city: a fine is written off even
    from an empty account and turns into debt (the `fine` sanction). In all
    other cases going negative is a bug of the calling code.
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
    """The common case: move from pocket to pocket."""
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
        #: Genesis is the only account allowed to be negative: its negative
        #: balance is the issued money supply.
        if account is not None and account.kind is AccountKind.GENESIS:
            continue
        current = await balance(session, account_id)
        if current + delta < 0:
            raise InsufficientFunds(
                f"на счёте {account_id} {current}, требуется {-delta}"
            )


async def money_supply(session: AsyncSession, currency: Currency = Currency.TK) -> int:
    """How much money is in the world.

    Equals minus the balance of genesis accounts. The I2 check in telemetry:
    the quantity changes only together with an explicit issue operation.
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
