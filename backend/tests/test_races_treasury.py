# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two citizens on one treasury (D-283).

A city's treasury became the source of every loan it gives, and a source is a
remainder like a purse: two citizens asking at once must not both be handed the
same coin. What guards it is the city's row, taken for the whole of the walk
from "how much have we got" to "here it is" -- the same row the line is read
under, and taken in the same place, so the two guards are one lock and not two.

Its own file rather than a section of `test_races_bank.py`: that one races what
the capital lends, and this races what a city lends of its own.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bank_kit import _city_with_turnover
from conftest import _slow
from src.constants import Catalog, Constants
from src.engine import bank, ledger, world
from src.engine import city as town
from src.engine.bank import loan as lending
from src.models.bank import Loan, LoanState
from src.models.city import Citizen
from src.models.identity import Identity
from src.models.ledger import AccountKind, PostingReason
from src.units import money


async def test_two_citizens_do_not_spend_one_treasury_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One purse, two askings: one is served, the other is told there is nothing.

    The city here has a hundred of its own and next to no line -- one deal of a
    single TC -- so it cannot make up a shortfall by borrowing, and the second
    loan has nowhere to come from. Without the row both read the same hundred,
    and the second is stopped by the ledger's own refusal to overdraw: the cap
    holds, but what the player gets is a crash where a refusal was owed.
    """

    city = await _city_with_turnover(session, catalog, turnover=1)
    treasury = await town.treasury(session, city)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=genesis.id,
        credit=treasury.id,
        amount=money(100),
        memo={},
    )
    two: list[uuid.UUID] = []
    for number in range(2):
        who = await world.create_identity(session, f"Горожанин-{number}-{uuid.uuid4().hex[:6]}")
        session.add(Citizen(identity_id=who.id, city_id=city.id))
        two.append(who.id)
    await bank.reserve_account(session)
    await session.commit()

    _slow(monkeypatch, lending, "city_line")

    async def take(identity_id: uuid.UUID) -> Loan:
        async with factory() as db, db.begin():
            borrower = await db.get(Identity, identity_id)
            return await bank.borrow(db, constants, catalog, borrower, 100)

    outcomes = await asyncio.gather(*(take(one) for one in two), return_exceptions=True)
    refused = [one for one in outcomes if isinstance(one, Exception)]
    assert len(refused) == 1, f"второму казна дать не может: {outcomes}"
    assert isinstance(refused[0], bank.TooMuch), refused[0]

    async with factory() as db:
        assert await town.treasury_balance(db, city) == 0, "казна отдала ровно то, что имела"
        lent = (
            (
                await db.execute(
                    select(Loan).where(
                        Loan.city_id == city.id,
                        Loan.identity_id.is_not(None),
                        Loan.state == LoanState.OPEN,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sum(one.principal for one in lent) == money(100), "роздано не больше, чем было"
