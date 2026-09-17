# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over the household meters' run.

One of the race files (see `test_races.py` for the family's method): here the
contended things are the ones the meter run holds for every household of the
world at once -- the meters, the city pools they draw and the holders' purses
(D-135, D-149) -- against a master drawing a pool for work, the pool tick
bringing every pool up to now, the automats' tick drawing purses, a second
run, and a holder paying off a debt. The city taking a node back races the
run in `test_races_meter_land.py`; the ground both build on is `utility_kit.py`.

The handshake is `automat_kit._until_blocked_by`: the side holding the
contended rows lets go only once the other side has provably walked into them.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import IRON, NAILS, _factory_floor, _learn, _lube_in, _pool_left, _until_blocked_by
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import automat, energy, ledger, utility, world
from src.models.automat import Automat as AutomatRow
from src.models.city import UtilityMeter
from src.models.identity import Body, Identity
from src.models.ledger import AccountKind, LedgerEntry, PostingReason
from src.models.world import Node
from src.units import money, money_str
from utility_kit import _grids, _meter, _open, _purse, _resident

#: What a master draws at the bench in the race below.
WORK = 50.0


async def test_a_bench_holding_a_later_pool_does_not_deadlock_the_meter_run_on_its_purse(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run takes every pool before any purse; a bench takes its pool and
    then its master's purse (`energy.draw_for_work`).

    Two cities. The holder's house stands in the city whose pool sorts first,
    and its meter is walked first; the other city's plot is billed from the
    other pool. The holder stands in that other city at a bench and holds its
    pool until the run has come to wait for it, then reaches for the purse.
    When the run billed each meter as it drew its pool, it held the holder's
    purse by then, and the two waited on each other. Now it holds only the
    first pool; the bench pays, commits, and the run goes on.
    """
    moment = datetime.now(UTC)
    hours = constants[R.ENERGY_METER_PERIOD]
    (first, first_home), (second, second_home) = await _grids(session, constants, catalog, 2)
    owner, body = await _resident(session, second_home, "Хозяин", funds=1000)
    first_home.owner_identity_id = owner.id
    meters = await _open(
        session, constants, [first_home, second_home], since=moment - timedelta(hours=hours)
    )
    first_pool = await energy.pool_of(session, constants, first_home)
    second_pool = await energy.pool_of(session, constants, second_home)
    assert first_pool is not None and second_pool is not None
    first_draw = utility.draw_for(constants, first_home, hours)
    bill = energy.price_at(constants, first_pool, first_draw)
    work = energy.price_at(constants, second_pool, WORK)
    assert bill > 0 and work > 0
    ids = (owner.id, body.id, first.id, second.id, [meter.id for meter in meters])
    await session.commit()
    owner_id, body_id, first_id, second_id, meter_ids = ids

    held = asyncio.Event()
    benches: list[AsyncSession] = []
    produced = energy.produce

    async def holding(db, *args, **kwargs):
        result = await produced(db, *args, **kwargs)
        if benches and db is benches[0] and not held.is_set():
            #: The bench holds its pool now; the purse comes next.
            held.set()
            await _until_blocked_by(factory, db)
        return result

    monkeypatch.setattr(energy, "produce", holding)

    async def bench() -> int:
        async with factory() as db, db.begin():
            benches.append(db)
            me = await db.get(Body, body_id)
            assert me is not None
            return await energy.draw_for_work(db, constants, me, WORK, goods="silicon", now=moment)

    async def run() -> int:
        await held.wait()
        async with factory() as db, db.begin():
            return await utility.run_meters(db, constants, now=moment)

    paid, listed = await asyncio.gather(bench(), run())

    assert paid == work
    assert listed == 2
    for meter_id in meter_ids:
        meter = await _meter(factory, meter_id)
        assert meter.counted_at == moment
        assert meter.debt == 0 and not meter.cut_off
    assert await _purse(factory, owner_id) == money(1000) - work - bill
    assert 100_000 - await _pool_left(factory, constants, first_id) == pytest.approx(
        first_draw, abs=0.01
    )
    second_draw = utility.draw_for(constants, second_home, hours)
    assert 100_000 - await _pool_left(factory, constants, second_id) == pytest.approx(
        second_draw + WORK, abs=0.01
    )


async def test_the_pool_tick_and_the_meter_run_take_pools_in_one_order(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two passes take several pools in one transaction: the pool tick
    bringing each up to now (`energy.tick_pools`) and the meter run drawing
    them. Both go by the grid node's id.

    Two cities whose meters were opened the other way round from their pools.
    The pool tick holds the first pool until the run has come to wait for it.
    When the run walked the meters as the table gave them, that first pool was
    the run's second: each held the pool the other wanted next.
    """
    moment = datetime.now(UTC)
    hours = constants[R.ENERGY_METER_PERIOD]
    (low, low_home), (high, high_home) = await _grids(session, constants, catalog, 2)
    #: Both plots civic: the treasury pays in energy (D-149), and no purse
    #: takes part -- the pools are the whole race.
    meters = await _open(
        session, constants, [high_home, low_home], since=moment - timedelta(hours=hours)
    )
    ids = (low.id, high.id, [meter.id for meter in meters])
    await session.commit()
    low_id, high_id, meter_ids = ids

    held = asyncio.Event()
    ticks: list[AsyncSession] = []
    produced = energy.produce

    async def holding(db, *args, **kwargs):
        result = await produced(db, *args, **kwargs)
        if ticks and db is ticks[0] and not held.is_set():
            held.set()
            await _until_blocked_by(factory, db)
        return result

    monkeypatch.setattr(energy, "produce", holding)

    async def tick() -> float:
        async with factory() as db, db.begin():
            ticks.append(db)
            return await energy.tick_pools(db, constants, now=moment)

    async def run() -> int:
        await held.wait()
        async with factory() as db, db.begin():
            return await utility.run_meters(db, constants, now=moment)

    _, listed = await asyncio.gather(tick(), run())

    assert listed == 2
    for meter_id in meter_ids:
        assert (await _meter(factory, meter_id)).counted_at == moment
    for grid_id, home in ((low_id, low_home), (high_id, high_home)):
        drawn = 100_000 - await _pool_left(factory, constants, grid_id)
        assert drawn == pytest.approx(utility.draw_for(constants, home, hours), abs=0.01)


async def test_paying_a_debt_during_the_meter_run_does_not_deadlock(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run takes the meters before any purse, and so does paying a debt
    off (`utility.pay`): the meter, then the holder's purse.

    A holder cut off for a debt pays it while the run bills the same house.
    The payment holds the purse it has just posted from until the run has
    come to wait for it. When the payment reached for the meter only after
    the purse, the run held that meter by then, and the two waited on each
    other. Now the payment holds the meter first; the run waits for it,
    finds the debt paid and bills the new hours from the purse.
    """
    moment = datetime.now(UTC)
    hours = constants[R.ENERGY_METER_PERIOD]
    ((_, home),) = await _grids(session, constants, catalog, 1)
    owner, _ = await _resident(session, home, "Должник", funds=1000)
    home.owner_identity_id = owner.id
    (meter,) = await _open(session, constants, [home], since=moment - timedelta(hours=hours))
    debt = money(5)
    meter.debt = debt
    meter.cut_off = True
    pool = await energy.pool_of(session, constants, home)
    bill = energy.price_at(constants, pool, utility.draw_for(constants, home, hours))
    assert bill > 0
    ids = (owner.id, home.id, meter.id)
    await session.commit()
    owner_id, home_id, meter_id = ids

    held = asyncio.Event()
    payments: list[AsyncSession] = []
    transferred = ledger.transfer

    async def holding(db, *args, **kwargs):
        result = await transferred(db, *args, **kwargs)
        if payments and db is payments[0] and not held.is_set():
            #: The payment holds the purse now; the meter's new state comes next.
            held.set()
            await _until_blocked_by(factory, db)
        return result

    monkeypatch.setattr(ledger, "transfer", holding)

    async def pay() -> int:
        async with factory() as db, db.begin():
            payments.append(db)
            me = await db.get(Identity, owner_id)
            house = await db.get(Node, home_id)
            assert me is not None and house is not None
            return await utility.pay(db, constants, me, house)

    async def run() -> int:
        await held.wait()
        async with factory() as db, db.begin():
            return await utility.run_meters(db, constants, now=moment)

    paid, listed = await asyncio.gather(pay(), run())

    assert paid == debt
    assert listed == 1
    settled = await _meter(factory, meter_id)
    assert settled.counted_at == moment
    assert settled.debt == 0 and not settled.cut_off
    assert await _purse(factory, owner_id) == money(1000) - debt - bill


async def test_a_debt_paid_during_the_meter_run_is_not_billed_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run reads each meter's debt and adds the new bill to it: it must
    read the meter under its lock, or a payment landing between the read and
    the write is undone.

    A holder cut off for a debt has enough to clear the debt, or to pay the
    new hours, but not both. They pay while the run is already drawing the
    pools. When the run read the meters unlocked, the payment committed there
    and then, the run found the purse short and wrote the debt it had read
    plus the new bill: the debt paid came back, and the house was lit with it.
    Now the payment waits for the run: the run bills the new hours from the
    purse, and the payment finds too little left for the debt.
    """
    moment = datetime.now(UTC)
    hours = constants[R.ENERGY_METER_PERIOD]
    ((_, home),) = await _grids(session, constants, catalog, 1)
    pool = await energy.pool_of(session, constants, home)
    bill = energy.price_at(constants, pool, utility.draw_for(constants, home, hours))
    assert bill > 1
    debt = 2 * bill
    #: Enough for either, not for both.
    funds = debt + bill // 2
    owner, _ = await _resident(session, home, "Должник")
    home.owner_identity_id = owner.id
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    account = await ledger.account_for(session, AccountKind.IDENTITY, owner.id)
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=account.id, amount=funds
    )
    (meter,) = await _open(session, constants, [home], since=moment - timedelta(hours=hours))
    meter.debt = debt
    meter.cut_off = True
    ids = (owner.id, home.id, meter.id)
    await session.commit()
    owner_id, home_id, meter_id = ids

    async def pay() -> int:
        async with factory() as db, db.begin():
            me = await db.get(Identity, owner_id)
            house = await db.get(Node, home_id)
            assert me is not None and house is not None
            return await utility.pay(db, constants, me, house)

    waited: list[bool] = []
    payments: list[asyncio.Future[int]] = []
    runs: list[AsyncSession] = []
    produced = energy.produce

    async def paying_midway(db, *args, **kwargs):
        result = await produced(db, *args, **kwargs)
        if runs and db is runs[0] and not payments:
            payments.append(asyncio.ensure_future(pay()))
            waited.append(await _until_blocked_by(factory, db, unless=payments[0]))
        return result

    monkeypatch.setattr(energy, "produce", paying_midway)

    async with factory() as db, db.begin():
        runs.append(db)
        assert await utility.run_meters(db, constants, now=moment) == 1
    (paid,) = await asyncio.gather(*payments, return_exceptions=True)

    settled = await _meter(factory, meter_id)
    spent = funds - await _purse(factory, owner_id)
    #: Whoever went first, the holder owes or has paid the debt and the new
    #: hours once each.
    assert settled.debt + spent == debt + bill, "a paid debt came back"
    assert settled.cut_off == (settled.debt > 0)
    assert waited == [True], "the payment waited for the run's meter"
    #: After the run, the debt was still there and the purse too short for it.
    assert isinstance(paid, utility.NotEnoughMoney)


async def test_two_meter_runs_bill_a_period_once(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """Two passes of the meter job at once -- two lanes of the worker, two
    periods due. The second waits for the first on the meters and reads the
    stamps it moved. Read free, the second counted the hours from the stamp it
    had read before the first moved it, and the holder paid for one day twice."""
    moment = datetime.now(UTC)
    hours = constants[R.ENERGY_METER_PERIOD]
    ((_, home),) = await _grids(session, constants, catalog, 1)
    owner, _ = await _resident(session, home, "Хозяин", funds=1000)
    home.owner_identity_id = owner.id
    (meter,) = await _open(session, constants, [home], since=moment - timedelta(hours=hours))
    account = await ledger.account_for(session, AccountKind.IDENTITY, owner.id)
    ids = (account.id, meter.id)
    await session.commit()
    purse_id, meter_id = ids

    held = asyncio.Event()

    async def holding() -> int:
        try:
            async with factory() as db, db.begin():
                listed = await utility.run_meters(db, constants, now=moment)
                held.set()
                await _until_blocked_by(factory, db)
                return listed
        finally:
            held.set()

    async def coming() -> int:
        await held.wait()
        async with factory() as db, db.begin():
            return await utility.run_meters(db, constants, now=moment)

    assert await asyncio.gather(holding(), coming()) == [1, 1]

    assert (await _meter(factory, meter_id)).counted_at == moment
    async with factory() as db:
        debits = await db.scalar(
            select(func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.account_id == purse_id, LedgerEntry.amount < 0)
        )
    assert debits == 1, "one period, one bill"


async def test_a_holder_of_two_houses_paying_mid_run_does_not_deadlock_it(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The run bills a holder's two houses: the first from their purse, the
    second's debt the holder is paying off at that moment -- holding its meter
    and reaching for the same purse. Meters taken as the walk came to them,
    the run held the purse from the first bill while it reached for the second
    meter, and Postgres killed one of the two. The run takes every meter before
    its first bill, so it waits holding no purse, and both go through."""
    moment = datetime.now(UTC)
    hours = constants[R.ENERGY_METER_PERIOD]
    ((centre, first),) = await _grids(session, constants, catalog, 1)
    second = await world.create_node(
        session, f"{first.key}.second", "Второй дом", area_m2=100, parent=centre
    )
    second.owner_city_id = first.owner_city_id
    owner, _ = await _resident(session, first, "Домовладелец", funds=1000)
    first.owner_identity_id = second.owner_identity_id = owner.id
    early, late = await _open(
        session, constants, [first, second], since=moment - timedelta(hours=hours)
    )
    debt = money(1)
    late.debt, late.cut_off = debt, True
    ids = (second.id, owner.id, early.id, late.id)
    await session.commit()
    second_id, owner_id, early_id, late_id = ids

    holding = asyncio.Event()

    async def pay() -> int:
        try:
            async with factory() as db, db.begin():
                #: The payment's own first lock, taken here so the run comes
                #: to wait on it before the payment reaches for the purse.
                await db.execute(
                    select(UtilityMeter.id).where(UtilityMeter.id == late_id).with_for_update()
                )
                holding.set()
                await _until_blocked_by(factory, db)
                me = await db.get(Identity, owner_id)
                house = await db.get(Node, second_id)
                assert me is not None and house is not None
                return await utility.pay(db, constants, me, house)
        finally:
            holding.set()

    async def run() -> int:
        await holding.wait()
        async with factory() as db, db.begin():
            return await utility.run_meters(db, constants, now=moment)

    paid, listed = await asyncio.gather(pay(), run())

    assert paid == debt
    assert listed == 2
    for meter_id in (early_id, late_id):
        settled = await _meter(factory, meter_id)
        assert settled.counted_at == moment
        assert settled.debt == 0 and not settled.cut_off, "paid, then billed from the purse"


async def test_the_automats_tick_and_the_meter_run_take_purses_in_one_order(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two holders of houses on one grid run machines in cities of their own.
    The automats' tick draws its pools and then the owners' purses in account
    order (`automat.bill.pay`); the run's meters put the later account's house
    first.

    When the run posted each bill in its meters' order, it held the later purse
    reaching for the earlier one the tick held, the tick reached for the later
    one, and Postgres killed one of the two. Now the run posts in the tick's
    order: it waits for the earlier purse holding no purse at all."""
    moment = datetime.now(UTC)
    hours = constants[R.ENERGY_METER_PERIOD]
    ((centre, home),) = await _grids(session, constants, catalog, 1)
    other = await world.create_node(
        session, f"{home.key}.other", "Соседний дом", area_m2=100, parent=centre
    )
    other.owner_city_id = home.owner_city_id

    holders = []
    for plot in (home, other):
        node, yard, identity, body, machine = await _factory_floor(session, constants)
        await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
        await _lube_in(session, yard, 1000)
        await _learn(session, identity, NAILS)
        row = await automat.program(session, constants, catalog, body, machine, NAILS)
        works = await energy.pool_of(session, constants, node)
        assert works is not None
        await ledger.account_for(session, AccountKind.CITY_TREASURY, works.node_id)
        plot.owner_identity_id = identity.id
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        holders.append((account.id, plot, identity.id, row))
    (early_purse, early_plot, _, _), (late_purse, late_plot, _, _) = sorted(
        holders, key=lambda holder: holder[0]
    )
    meters = await _open(
        session, constants, [late_plot, early_plot], since=moment - timedelta(hours=hours)
    )
    worked_to = max(row.counted_at for *_, row in holders) + timedelta(hours=2)
    ids = ([meter.id for meter in meters], [row.id for *_, row in holders])
    await session.commit()
    meter_ids, row_ids = ids

    ticks: list[AsyncSession] = []
    held = asyncio.Event()
    posted = ledger.transfer

    async def holding(db, *args, **kwargs):
        result = await posted(db, *args, **kwargs)
        if ticks and db is ticks[0] and not held.is_set():
            #: The tick holds the earlier purse now; the later one comes next.
            held.set()
            await _until_blocked_by(factory, db)
        return result

    monkeypatch.setattr(ledger, "transfer", holding)

    async def tick() -> float:
        try:
            async with factory() as db, db.begin():
                ticks.append(db)
                return await automat.tick_automats(db, constants, now=worked_to)
        finally:
            held.set()

    async def run() -> int:
        await held.wait()
        async with factory() as db, db.begin():
            return await utility.run_meters(db, constants, now=moment)

    made, listed = await asyncio.gather(tick(), run())

    assert made > 0
    assert listed == 2
    for meter_id in meter_ids:
        assert (await _meter(factory, meter_id)).counted_at == moment
    async with factory() as db:
        for row_id in row_ids:
            row = await db.get(AutomatRow, row_id)
            assert row is not None and row.counted_at == worked_to
        for purse_id in (early_purse, late_purse):
            debits = await db.scalar(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.account_id == purse_id, LedgerEntry.amount < 0)
            )
            assert debits == 2, "the machine and the house, each once"


async def test_a_purse_spent_as_the_debt_is_paid_is_refused_as_not_enough_money(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paying a debt off asks the purse under the purse's lock, inside the
    posting. The holder's purse is emptied by another command between the
    payment's look and its posting: when the payment read the balance before
    the lock, it found enough, and the holder was told the ledger's own
    refusal. Now they are told the utility's, with what the purse really has."""
    ((_, home),) = await _grids(session, constants, catalog, 1)
    owner, _ = await _resident(session, home, "Должник", funds=10)
    home.owner_identity_id = owner.id
    (meter,) = await _open(session, constants, [home], since=datetime.now(UTC))
    meter.debt, meter.cut_off = money(5), True
    account = await ledger.account_for(session, AccountKind.IDENTITY, owner.id)
    ids = (owner.id, home.id, meter.id, account.id)
    await session.commit()
    owner_id, home_id, meter_id, purse_id = ids

    posted = ledger.transfer
    payments: list[AsyncSession] = []

    async def spent_first(db, *args, **kwargs):
        if payments and db is payments[0] and kwargs.get("debit") == purse_id:
            async with factory() as elsewhere, elsewhere.begin():
                shop = await ledger.account_for(elsewhere, AccountKind.IDENTITY, uuid.uuid4())
                await posted(
                    elsewhere,
                    PostingReason.TRANSFER,
                    debit=purse_id,
                    credit=shop.id,
                    amount=money(8),
                    memo={},
                )
        return await posted(db, *args, **kwargs)

    monkeypatch.setattr(ledger, "transfer", spent_first)

    with pytest.raises(utility.NotEnoughMoney) as refused:
        async with factory() as db, db.begin():
            payments.append(db)
            me = await db.get(Identity, owner_id)
            house = await db.get(Node, home_id)
            assert me is not None and house is not None
            await utility.pay(db, constants, me, house)

    assert refused.value.params["have"] == money_str(money(2))
    settled = await _meter(factory, meter_id)
    assert settled.debt == money(5) and settled.cut_off
    assert await _purse(factory, owner_id) == money(2)
