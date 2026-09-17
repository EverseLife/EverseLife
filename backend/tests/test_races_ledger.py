# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over the accounts a posting touches.

One of the race files (see `test_races.py` for the family's method, and the
race of two debits of one coin there): here a debit of an account meets a
credit into it. A credit posting takes the credited account `FOR KEY SHARE`
through `ledger_entry.account_id`'s foreign key, so the lock `ledger.post`
queues debits behind must let that through, or a purse paying a treasury and
the treasury paying the purse each hold one row and wait on the other.

The handshake is `conftest._until_blocked_by`: the side holding the
contended row lets go only once the other side has provably walked into it --
or walked past it.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _until_blocked_by
from src.engine import ledger
from src.models.ledger import AccountKind, PostingReason
from src.units import money


async def _funded(session: AsyncSession, kind: AccountKind, amount: int) -> uuid.UUID:
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    account = await ledger.account_for(session, kind, uuid.uuid4())
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=account.id, amount=amount
    )
    return account.id


async def _balance(factory: async_sessionmaker[AsyncSession], account_id: uuid.UUID) -> int:
    async with factory() as db:
        return await ledger.balance(db, account_id)


async def test_a_purse_paying_a_treasury_that_pays_it_back_does_not_deadlock(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A holder pays their bill into the treasury while the treasury pays them.

    The bill locks the purse and stops there, before its postings. The payout
    locks the treasury and posts into the purse. When debits were locked
    `FOR UPDATE`, the payout's key share on the purse waited for the bill, the
    bill's key share on the treasury then waited for the payout, and Postgres
    killed one of the two. `FOR NO KEY UPDATE` lets a key share through: the
    payout walks past the held purse and commits, and the bill goes on.
    """
    purse = await _funded(session, AccountKind.IDENTITY, money(1000))
    treasury = await _funded(session, AccountKind.CITY_TREASURY, money(1000))
    await session.commit()

    holding = asyncio.Event()
    check = ledger._check_funds

    async def payout() -> None:
        await holding.wait()
        async with factory() as db, db.begin():
            await ledger.transfer(
                db, PostingReason.TRANSFER, debit=treasury, credit=purse, amount=money(30)
            )

    back = asyncio.ensure_future(payout())

    async def held(db: AsyncSession, postings) -> None:
        #: `_check_funds` runs under the debit locks and before the postings:
        #: the bill holds the purse here and has not yet touched the treasury.
        await check(db, postings)
        if not holding.is_set():
            holding.set()
            await _until_blocked_by(factory, db, unless=back)

    monkeypatch.setattr(ledger, "_check_funds", held)

    async def bill() -> None:
        try:
            async with factory() as db, db.begin():
                await ledger.transfer(
                    db, PostingReason.ENERGY_BILL, debit=purse, credit=treasury, amount=money(100)
                )
        finally:
            holding.set()

    outcomes = await asyncio.gather(bill(), back, return_exceptions=True)

    assert not [one for one in outcomes if isinstance(one, BaseException)], outcomes
    assert await _balance(factory, purse) == money(1000 - 100 + 30)
    assert await _balance(factory, treasury) == money(1000 + 100 - 30)
