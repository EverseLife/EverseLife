# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Belonging as a lock on money (D-281).

Belonging is not money, but it now guards money twice over: only the city one
belongs to lends, and one does not leave it owing. Two sessions on the same
citizenship -- one borrowing, one walking out -- and two on the same city line,
which past D-281 is the loan's only brake rather than a fork in the price.
Both pairs are worth exactly as much as the row lock under them.

Its own file rather than a fifth section of `test_races.py`: that one is about
the arithmetic of money, orders and reserves, and this is about the records
that decide who may touch them.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bank_kit import _city_with_turnover, _enrol
from conftest import _slow
from src.constants import Catalog, Constants
from src.engine import bank, world
from src.engine import city as town
from src.engine.bank import loan as lending
from src.models.bank import Loan, LoanState
from src.models.city import Citizen
from src.models.identity import Identity
from src.units import money


async def test_a_loan_and_an_exit_do_not_cross(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whichever is second sees the first, and never both.

    Only the bank's own reading is held back: `borrow` looks the citizenship up
    through the city package, `leave` calls its neighbour by name -- so the
    pause widens the window on one side, which is the whole of the race.
    """

    who = await world.create_identity(session, f"Уходящий-{uuid.uuid4().hex[:6]}")
    await _enrol(session, who)
    await session.commit()
    _slow(monkeypatch, town, "citizenship")

    async def take() -> Loan:
        async with factory() as db, db.begin():
            borrower = await db.get(Identity, who.id)
            return await bank.borrow(db, constants, catalog, borrower, 100)

    async def go() -> None:
        async with factory() as db, db.begin():
            leaver = await db.get(Identity, who.id)
            await town.leave(db, leaver)

    outcomes = await asyncio.gather(take(), go(), return_exceptions=True)
    refused = [one for one in outcomes if isinstance(one, Exception)]
    assert len(refused) == 1, f"кто-то один обязан уйти ни с чем: {outcomes}"
    assert isinstance(refused[0], (town.InDebt, bank.NotOurs)), refused[0]

    async with factory() as db:
        loans = (
            (
                await db.execute(
                    select(Loan).where(Loan.identity_id == who.id, Loan.state == LoanState.OPEN)
                )
            )
            .scalars()
            .all()
        )
        entry = (
            (await db.execute(select(Citizen).where(Citizen.identity_id == who.id)))
            .scalars()
            .first()
        )
        #: The invariant, not the winner: an open loan means the city still has
        #: its citizen, and a citizenship given up means nothing is owed.
        assert bool(loans) == (entry is not None), (
            "кредит без гражданства оставил бы город отвечать за чужого долга"
        )


async def test_two_citizens_do_not_overdraw_one_city_line(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A city's line is a ceiling now, and a ceiling is a remainder (CLAUDE.md).

    Before D-281 an overflowing line was a matter of price: the loan went to
    the capital direct and dearer. Now there is nothing past it, so two
    citizens of one city reading the same free remainder and both taking it
    would leave the city answering for more than the capital ever allowed --
    the very shape the treasury's own borrowing already locks against
    (`works_city.credit`).
    """

    #: The line is `bank.debt_to_turnover_cap` of turnover -- 300% of 50 TC is
    #: 150, which is one loan of a hundred and not two.
    city = await _city_with_turnover(session, turnover=50)
    two: list[uuid.UUID] = []
    for number in range(2):
        who = await world.create_identity(session, f"Заёмщик-{number}-{uuid.uuid4().hex[:6]}")
        session.add(Citizen(identity_id=who.id, city_id=city.id))
        two.append(who.id)
    await session.flush()
    permitted, _, free = await bank.city_line(session, constants, city)
    assert free >= money(100), "линия покрывает один заём и не покрывает два"
    assert free < money(200)
    #: The reserve account is opened here rather than by the first loan to
    #: reach it: two transactions creating the world's one reserve at once is
    #: a different race, and it would answer this test's question with the
    #: wrong exception.
    await bank.reserve_account(session)
    await session.commit()
    _slow(monkeypatch, lending, "city_line")

    async def take(identity_id: uuid.UUID) -> Loan:
        async with factory() as db, db.begin():
            borrower = await db.get(Identity, identity_id)
            return await lending.borrow(db, constants, catalog, borrower, 100)

    outcomes = await asyncio.gather(*(take(one) for one in two), return_exceptions=True)
    refused = [one for one in outcomes if isinstance(one, Exception)]
    assert len(refused) == 1, f"второй заёмщик должен уйти ни с чем: {outcomes}"
    assert isinstance(refused[0], bank.TooMuch), refused[0]

    async with factory() as db:
        assert await bank.city_outstanding(db, city) <= permitted, (
            "город отвечает не больше, чем разрешила ему столица"
        )
