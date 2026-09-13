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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agro_kit import LUBRICANT, field, liquid_in, plot_of, programmed, second_now
from automat_kit import IRON, NAILS, _factory_floor, _learn, _lube_in
from src.constants import Catalog, Constants, current, current_catalog
from src.constants import registry as R
from src.engine import agro, automat, energy, ledger, world
from src.engine.automat import bill as energy_bill
from src.models.agro import FieldAutomat, FieldAutomatPlot
from src.models.farm import Plot, PlotState
from src.models.identity import Body
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Node
from src.units import amount_float


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
    my_pool = await energy.pool_of(session, constants, mine.node)
    assert my_pool is not None
    stored = float(my_pool.stored)
    ids = (my_row.id, my_plot.id, their_plot.id, account.id, mine.node.id)
    await session.commit()
    row_id, my_plot_id, their_plot_id, account_id, my_node_id = ids

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
        done = (await agro.tick_machines(db, current(), now=later)).actions

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
        here = await db.get(Node, my_node_id)
        assert here is not None
        pool = await energy.pool_of(db, constants, here, create=False)
        assert pool is not None
        assert float(pool.stored) == pytest.approx(stored), "whoever cannot pay does not burn"


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
    pool = await energy.pool_of(session, constants, place.node)
    assert pool is not None
    stored = float(pool.stored)
    worn = (float(place.machine.condition), float(place.machine.wear_remainder))
    ids = (row.id, first.id, second.id, account.id, place.body.id, place.machine.id, place.node.id)
    await session.commit()
    row_id, first_id, second_id, account_id, body_id, machine_id, node_id = ids

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
        given = (
            (
                await db.execute(
                    select(FieldAutomatPlot.plot_id).where(FieldAutomatPlot.automat_id == row_id)
                )
            )
            .scalars()
            .all()
        )
        assert list(given) == [second_id], "the new programme is taken all the same"
        machine_now = await db.get(Item, machine_id)
        assert machine_now is not None
        assert (float(machine_now.condition), float(machine_now.wear_remainder)) != worn, (
            "the machine wore through the minute it stood"
        )
        here = await db.get(Node, node_id)
        assert here is not None
        left = await energy.pool_of(db, constants, here, create=False)
        assert left is not None
        assert float(left.stored) == pytest.approx(stored), "whoever cannot pay does not burn"


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
        done = (await agro.tick_machines(db, current(), now=later)).actions

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


async def test_the_family_shares_one_pool_and_takes_turns_at_its_last_minute(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """An automat and a field automaton on one pool that holds one and a half
    machine-minutes, two minutes running. They promise on one tab, so the pool
    is never promised twice; the field automaton takes its hours whole or not
    at all; and the first to promise changes with the minute, so neither kind
    drinks the pool every minute (`automat.run._pass`, D-339 p. 8)."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    moment = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], moment)
    await _learn(session, place.identity, NAILS)
    await world.grant_item(session, place.yard, IRON, amount=1000, quality=60, origin="тест")
    station = await world.grant_item(session, place.yard, "auto_station", quality=70, origin="тест")
    await automat.program(session, constants, catalog, place.body, station, NAILS, now=moment)
    pool = await energy.pool_of(session, constants, place.node)
    assert pool is not None
    minute = constants[R.AGRO_ENERGY_PER_HOUR] / 60
    assert constants[R.AUTO_ENERGY_PER_HOUR] / 60 == pytest.approx(minute)

    for step in (1, 2):
        pool.stored = Decimal(str(minute * 1.5))
        await session.flush()
        now = moment + timedelta(minutes=step)
        await agro.tick_machines(session, constants, now=now)
        await session.refresh(row)
        await session.refresh(pool)
        if now.minute % 2:
            #: The field automaton first: its whole minute, the automat half of one.
            assert row.trouble != "no_power", "the field automaton promised first"
            assert float(pool.stored) == pytest.approx(0, abs=0.001)
        else:
            #: The automat first: its whole minute; half a minute is no minute
            #: for the field automaton, and the half stays in the pool.
            assert row.trouble == "no_power", "half a minute is not promised"
            assert float(pool.stored) == pytest.approx(minute * 0.5, abs=0.001)


async def test_a_factory_owners_purse_emptied_under_the_family_does_not_count_a_field_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One owner's automat and another owner's field automaton in one pass. The
    automat's owner empties the purse before the draw: the whole pass goes back
    and runs again -- the automat standing, the field automaton ploughing once,
    burning one minute's lubricant and billed for one minute, not two."""
    floor, floor_yard, maker, maker_body, station = await _factory_floor(session, constants)
    await world.grant_item(session, floor_yard, IRON, amount=1000, quality=60, origin="тест")
    maker_oil = await _lube_in(session, floor_yard, 100)
    await _learn(session, maker, NAILS)
    moment = second_now()
    await automat.program(session, constants, catalog, maker_body, station, NAILS, now=moment)
    maker_account = await ledger.account_for(session, AccountKind.IDENTITY, maker.id)

    place = await field(session, constants)
    oil = await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], moment)
    account = await ledger.account_for(session, AccountKind.IDENTITY, place.identity.id)
    purse = await ledger.balance(session, account.id)
    pool = await energy.pool_of(session, constants, place.node)
    assert pool is not None
    price = energy.price_at(constants, pool, constants[R.AGRO_ENERGY_PER_HOUR] / 60)
    ids = (maker_account.id, maker_oil.id, floor_yard.id, plot.id, oil.id, account.id)
    await session.commit()
    maker_account_id, maker_oil_id, floor_yard_id, plot_id, oil_id, account_id = ids

    drawn = energy_bill.pay
    spent: list[bool] = []

    async def spent_first(*args, **kwargs):
        if not spent:
            await _empty_purse(factory, maker_account_id)
            spent.append(True)
        return await drawn(*args, **kwargs)

    monkeypatch.setattr(energy_bill, "pay", spent_first)
    later = moment + timedelta(minutes=1)
    async with factory() as db, db.begin():
        minute_done = await agro.tick_machines(db, current(), now=later)

    assert spent, "the maker's purse was emptied between the promise and the draw"
    assert minute_done.actions == 1, "the field automaton's ploughing counted once"
    async with factory() as db:
        ploughed = await db.get(Plot, plot_id)
        assert ploughed is not None and ploughed.state is PlotState.PLOWED
        left = await db.get(Item, oil_id)
        assert left is not None
        assert amount_float(left.amount) == pytest.approx(
            100 - constants[R.AUTO_LUBE_PER_HOUR] / 60, abs=0.002
        ), "one minute's lubricant, not two"
        assert purse - await ledger.balance(db, account_id) == pytest.approx(price, abs=1)
        assert await ledger.balance(db, maker_account_id) == 0
        maker_left = await db.get(Item, maker_oil_id)
        assert maker_left is not None and amount_float(maker_left.amount) == pytest.approx(100)
        nails = (
            (
                await db.execute(
                    select(Item).where(Item.container_id == floor_yard_id, Item.type_key == NAILS)
                )
            )
            .scalars()
            .all()
        )
        assert not nails, "the unpaid factory made nothing"
