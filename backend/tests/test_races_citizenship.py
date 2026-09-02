# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A loan and an exit at the same citizenship (D-281).

Belonging is not money, but it now guards money: only the city one belongs to
lends, and one does not leave it owing. Two sessions on the same row -- one
borrowing, one walking out -- and the pair of rules is only worth as much as
the row lock under them: without it the bank reads a citizenship the exit is
about to delete, and the city is left answering on its line with the capital
for somebody who is no longer its citizen.

Its own file rather than a fifth section of `test_races.py`: that one is about
the arithmetic of money, orders and reserves, and this is about a record that
decides who may touch them.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bank_kit import _enrol
from conftest import _slow
from src.constants import Catalog, Constants
from src.engine import bank, world
from src.engine import city as town
from src.models.bank import Loan, LoanState
from src.models.city import Citizen


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
            borrower = await db.get(type(who), who.id)
            return await bank.borrow(db, constants, catalog, borrower, 100)

    async def go() -> None:
        async with factory() as db, db.begin():
            leaver = await db.get(type(who), who.id)
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
