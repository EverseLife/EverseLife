# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The field automaton's energy under a second session (D-339 p. 8): the
family's rule for what happens between the promise and the draw (D-253, the
owner, 2026-09-13, `20-systems/12-energy.md`). A purse another transaction
empties is not forgiven -- the pass, or the command's advance, runs again with
it empty; a pool another transaction drinks leaves the work done and bills
only what it gave.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agro_kit import LUBRICANT, field, liquid_in, plot_of, programmed, second_now
from src.constants import Catalog, Constants, current, current_catalog
from src.constants import registry as R
from src.engine import agro, energy, ledger
from src.engine.automat import bill as energy_bill
from src.models.agro import FieldAutomat
from src.models.farm import Plot, PlotState
from src.models.identity import Body
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Node


async def _empty_purse(factory: async_sessionmaker[AsyncSession], account_id: uuid.UUID) -> None:
    """The owner's own spending, committing in another transaction."""
    async with factory() as elsewhere, elsewhere.begin():
        purse = await ledger.balance(elsewhere, account_id)
        shop = await ledger.account_for(elsewhere, AccountKind.IDENTITY, uuid.uuid4())
        await ledger.transfer(
            elsewhere,
            PostingReason.TRANSFER,
            debit=account_id,
            credit=shop.id,
            amount=purse,
            memo={},
        )


async def test_a_purse_emptied_under_the_fields_tick_buys_no_free_minute(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The purse empties after every machine worked and before the first
    draw: the pass goes back and runs again with it empty -- nothing ploughed,
    the machine standing with `no_power` and the minute gone -- while the
    neighbour's machine beside it ploughs in the same step."""
    mine = await field(session, constants)
    await liquid_in(session, mine.yard, LUBRICANT, 100)
    my_plot = await plot_of(session, constants, mine.body, name="моё")
    theirs = await field(session, constants)
    await liquid_in(session, theirs.yard, LUBRICANT, 100)
    their_plot = await plot_of(session, constants, theirs.body, name="соседское")
    moment = second_now()
    my_row = await programmed(
        session, constants, catalog, mine, [{"do": "plow"}], [my_plot], moment
    )
    await programmed(session, constants, catalog, theirs, [{"do": "plow"}], [their_plot], moment)
    account = await ledger.account_for(session, AccountKind.IDENTITY, mine.identity.id)
    ids = (my_row.id, my_plot.id, their_plot.id, account.id)
    await session.commit()
    row_id, my_plot_id, their_plot_id, account_id = ids

    drawn = energy_bill.pay
    spent: list[bool] = []

    async def spent_first(*args, **kwargs):
        if not spent:
            await _empty_purse(factory, account_id)
            spent.append(True)
        return await drawn(*args, **kwargs)

    monkeypatch.setattr(energy_bill, "pay", spent_first)
    later = moment + timedelta(minutes=1)
    async with factory() as db, db.begin():
        done = await agro.tick_fields(db, current(), now=later)

    assert spent, "the purse was emptied between the promise and the draw"
    assert done == 1, "the neighbour ploughed in the same step"
    async with factory() as db:
        row = await db.get(FieldAutomat, row_id)
        assert row is not None
        assert row.trouble == "no_power"
        assert row.counted_at == later, "the unpaid minute is gone, not banked"
        mine_now = await db.get(Plot, my_plot_id)
        theirs_now = await db.get(Plot, their_plot_id)
        assert mine_now is not None and theirs_now is not None
        assert mine_now.state is PlotState.IDLE, "nothing ploughed for free"
        assert theirs_now.state is PlotState.PLOWED
        assert await ledger.balance(db, account_id) == 0


async def test_a_purse_emptied_under_a_command_loses_the_minute_and_the_command_goes_on(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting a new programme works the old one up to now first. A purse
    emptied between that settling's promise and its draw sends the settling
    back to run again with the purse empty: the old programme ploughs
    nothing, the minute is gone, and the new programme is taken all the same."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    first = await plot_of(session, constants, place.body, name="первое")
    second = await plot_of(session, constants, place.body, name="второе")
    moment = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [first], moment)
    account = await ledger.account_for(session, AccountKind.IDENTITY, place.identity.id)
    ids = (row.id, first.id, second.id, account.id, place.body.id, place.machine.id)
    await session.commit()
    row_id, first_id, second_id, account_id, body_id, machine_id = ids

    drawn = energy_bill.pay
    spent: list[bool] = []

    async def spent_first(*args, **kwargs):
        if not spent:
            await _empty_purse(factory, account_id)
            spent.append(True)
        return await drawn(*args, **kwargs)

    monkeypatch.setattr(energy_bill, "pay", spent_first)
    later = moment + timedelta(minutes=1)
    async with factory() as db, db.begin():
        body = await db.get(Body, body_id)
        machine = await db.get(Item, machine_id)
        assert body is not None and machine is not None
        await agro.program(
            db,
            current(),
            current_catalog(),
            body,
            machine,
            steps=[{"do": "plow"}],
            plots=[str(second_id)],
            seeds=None,
            fertilizer=None,
            harvest=None,
            now=later,
        )

    assert spent, "the purse was emptied under the command's settling"
    async with factory() as db:
        settled = await db.get(FieldAutomat, row_id)
        assert settled is not None
        assert settled.counted_at == later, "the unpaid minute is gone, not banked"
        old = await db.get(Plot, first_id)
        assert old is not None and old.state is PlotState.IDLE, "nothing ploughed for free"
        assert await ledger.balance(db, account_id) == 0


async def test_a_pool_drunk_under_the_fields_tick_is_billed_for_what_it_gave(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crafter on the same grid drinks the pool down to half the machine's
    minute between the promise and the draw: the ploughing stays done, the
    pool gives the half it has and stops at nought, and the owner is billed
    for that half and not a unit more."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    moment = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], moment)
    account = await ledger.account_for(session, AccountKind.IDENTITY, place.identity.id)
    purse = await ledger.balance(session, account.id)
    ids = (row.id, plot.id, account.id, place.node.id)
    await session.commit()
    row_id, plot_id, account_id, node_id = ids
    half = constants[R.AGRO_ENERGY_PER_HOUR] / 60 / 2

    drawn = energy_bill.pay
    drunk: list[bool] = []

    async def drunk_first(*args, **kwargs):
        if not drunk:
            async with factory() as elsewhere, elsewhere.begin():
                here = await elsewhere.get(Node, node_id)
                assert here is not None
                pool = await energy.pool_of(elsewhere, constants, here, lock=True)
                assert pool is not None
                pool.stored = Decimal(str(half))
            drunk.append(True)
        return await drawn(*args, **kwargs)

    monkeypatch.setattr(energy_bill, "pay", drunk_first)
    later = moment + timedelta(minutes=1)
    async with factory() as db, db.begin():
        done = await agro.tick_fields(db, current(), now=later)

    assert drunk and done == 1, "the ploughing was done"
    async with factory() as db:
        settled = await db.get(FieldAutomat, row_id)
        assert settled is not None and settled.trouble is None
        ploughed = await db.get(Plot, plot_id)
        assert ploughed is not None and ploughed.state is PlotState.PLOWED
        here = await db.get(Node, node_id)
        assert here is not None
        pool = await energy.pool_of(db, constants, here, create=False)
        assert pool is not None
        assert float(pool.stored) == pytest.approx(0, abs=0.001)
        billed = purse - await ledger.balance(db, account_id)
        assert billed == pytest.approx(energy.price_at(constants, pool, half), abs=1)
        assert billed > 0
