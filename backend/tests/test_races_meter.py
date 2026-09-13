# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over the household meters' run.

One of the race files (see `test_races.py` for the family's method): here the
contended things are the ones the meter run holds for every household of the
world at once -- the meters, the city pools they draw and the holders' purses
(D-135, D-149) -- against a master drawing a pool for work, the pool tick
bringing every pool up to now, a holder paying off a debt, and the city
taking a node back.

The handshake is `automat_kit._until_blocked_by`: the side holding the
contended rows lets go only once the other side has provably walked into them.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import _pool_left, _until_blocked_by
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import energy, ledger, utility, world
from src.engine.city import land as city_land
from src.models.city import City, UtilityMeter
from src.models.event import Event, EventKind
from src.models.identity import Body, Identity
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Node
from src.units import money
from utility_kit import _city, _pool, _resident

#: What a master draws at the bench in the race below.
WORK = 50.0


async def _grids(session: AsyncSession, constants: Constants, catalog: Catalog, count: int):
    """Cities with a charged pool each and a plot in each, in their grid node's order.

    Each treasury's account is opened here: opened by the first bill instead,
    two transactions would meet on its insert, and the race would be about
    that row rather than the order under test.
    """
    made = []
    for number in range(count):
        _, delegate, home = await _city(session, catalog, name=f"Столица {number}")
        await _pool(session, constants, home, 100_000)
        await ledger.account_for(session, AccountKind.CITY_TREASURY, delegate.id)
        made.append((delegate, home))
    return sorted(made, key=lambda one: one[0].id)


async def _open(
    session: AsyncSession, constants: Constants, homes: list[Node], since: datetime
) -> list[UtilityMeter]:
    """Open the meters one by one in this order, each counted from `since`.

    Written in this order and with ids sorting in it too: a table read with no
    `order_by` comes back in the order its rows were written, and a run that
    walks its meters by id alone goes the same way -- so either is the order
    the races below set against the pools'.
    """
    meters = []
    for meter_id, home in zip(sorted(uuid.uuid4() for _ in homes), homes, strict=True):
        meter = UtilityMeter(id=meter_id, node_id=home.id, counted_at=since)
        session.add(meter)
        await session.flush()
        meters.append(meter)
    #: Nothing else in these worlds carries a meter, so no other meter slips
    #: into the run's order.
    assert await utility.ensure_meters(session, constants) == 0
    return meters


async def _purse(factory: async_sessionmaker[AsyncSession], identity_id) -> int:
    async with factory() as db:
        account = await ledger.find_account(db, AccountKind.IDENTITY, identity_id)
        assert account is not None
        return await ledger.balance(db, account.id)


async def _meter(factory: async_sessionmaker[AsyncSession], meter_id) -> UtilityMeter:
    async with factory() as db:
        meter = await db.get(UtilityMeter, meter_id)
        assert meter is not None
        return meter


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


async def _held_by(factory: async_sessionmaker[AsyncSession], node_id, meter_id):
    """Who holds the node, and what its meter owes: `(holder, debt, cut_off)`."""
    async with factory() as db:
        node = await db.get(Node, node_id)
        meter = await db.get(UtilityMeter, meter_id)
        assert node is not None and meter is not None
        return node.owner_identity_id, meter.debt, meter.cut_off


def _nobody_billed_for_civic(state) -> None:
    holder, debt, cut_off = state
    if holder is None:
        #: A node the city holds has nobody to bill (D-149): a debt left on it
        #: is never paid and its cut-off never lifted.
        assert debt == 0 and not cut_off, "a debt on a node nobody can be billed for"


@pytest.mark.parametrize("how", ["cede", "reclaim", "centre"])
async def test_land_handed_back_during_the_meter_run_is_not_left_in_debt(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
    how: str,
) -> None:
    """Handing a node back to the city settles its meter (`city/land.py`), and
    the meter run adds its bill to the same meter: whoever comes second must
    read it under its lock.

    A holder without a coin, whose house the run is about to bill into debt.
    The house goes back to the city while the run is already drawing the pools
    -- given up by the holder (`cede`), or taken back as a location that was
    never a plot (`reclaim`), the city's own node among them (`centre`). When
    the hand-over read the meter unlocked, it found no debt, committed there
    and then, and the run -- billing the holder it had read -- wrote the debt
    onto a node the city now held. Now the hand-over waits for the run's
    meter: after it, `cede` refuses the debtor, and `reclaim` clears what the
    run wrote.

    And it waits holding nothing the run reaches for. A meter row updated twice
    in one transaction takes its node `FOR KEY SHARE`, and so does the city's
    pool, brought up to now and then drawn: a hand-over holding the node while
    it waited for the meter deadlocked with the run.
    """
    #: A minute on from the pool's last count, so the run brings it up to now
    #: and writes it twice.
    moment = datetime.now(UTC) + timedelta(minutes=1)
    hours = constants[R.ENERGY_METER_PERIOD]
    ((centre, home),) = await _grids(session, constants, catalog, 1)
    owner, body = await _resident(session, home, "Хозяин")
    if how == "centre":
        centre.owner_city_id = home.owner_city_id
        home.owner_city_id = None
        home = centre
    home.owner_identity_id = owner.id
    if how != "cede":
        #: Not a plot: the city's own location, handed out by mistake (D-282).
        home.properties = {}
    (meter,) = await _open(session, constants, [home], since=moment - timedelta(hours=hours))
    ids = (body.id, home.id, home.owner_city_id, meter.id)
    await session.commit()
    body_id, home_id, city_id, meter_id = ids

    async def hand_back():
        async with factory() as db, db.begin():
            house = await db.get(Node, home_id)
            assert house is not None
            if how == "cede":
                return await town.cede(db, await db.get(Body, body_id), house)
            city = await db.get(City, city_id)
            assert city is not None
            return await town.reclaim(db, house, city)

    waited: list[bool] = []
    handovers: list[asyncio.Future] = []
    runs: list[AsyncSession] = []
    produced = energy.produce

    async def handing_back_midway(db, *args, **kwargs):
        result = await produced(db, *args, **kwargs)
        if runs and db is runs[0] and not handovers:
            handovers.append(asyncio.ensure_future(hand_back()))
            waited.append(await _until_blocked_by(factory, db, unless=handovers[0]))
        return result

    monkeypatch.setattr(energy, "produce", handing_back_midway)

    async with factory() as db, db.begin():
        runs.append(db)
        assert await utility.run_meters(db, constants, now=moment) == 1
    (handed,) = await asyncio.gather(*handovers, return_exceptions=True)

    state = await _held_by(factory, home_id, meter_id)
    _nobody_billed_for_civic(state)
    assert waited == [True], "the hand-over waited for the run's meter"
    if how == "cede":
        assert isinstance(handed, town.CityError) and handed.key == "city-land-debt"
        assert state[0] is not None and state[1] > 0, "the debtor keeps the plot and the debt"
    else:
        assert handed is True
        assert state[0] is None


async def test_a_debt_the_run_writes_during_a_cede_is_not_written_off(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`cede` refuses a debtor (D-149), so it must read the debt under the
    meter's lock and keep it to the hand-over.

    A holder without a coin gives the plot up, and the run comes to bill the
    house between the refusal's check and the hand-over -- which clears
    whatever debt a node brings back to the city. When the check read the
    meter unlocked, it found none, the run wrote the holder's debt and
    committed, and the hand-over wrote it off: the holder walked away from a
    bill nobody will ever pay. Now the run waits for the hand-over, and bills
    the city's plot in energy after it.
    """
    moment = datetime.now(UTC)
    hours = constants[R.ENERGY_METER_PERIOD]
    ((_, home),) = await _grids(session, constants, catalog, 1)
    owner, body = await _resident(session, home, "Хозяин")
    home.owner_identity_id = owner.id
    (meter,) = await _open(session, constants, [home], since=moment - timedelta(hours=hours))
    ids = (body.id, home.id, meter.id)
    await session.commit()
    body_id, home_id, meter_id = ids

    async def run() -> int:
        async with factory() as db, db.begin():
            return await utility.run_meters(db, constants, now=moment)

    waited: list[bool] = []
    runs: list[asyncio.Future[int]] = []
    handed = city_land._into_the_citys_hands

    async def billed_first(db, *args, **kwargs):
        if not runs:
            runs.append(asyncio.ensure_future(run()))
            waited.append(await _until_blocked_by(factory, db, unless=runs[0]))
        return await handed(db, *args, **kwargs)

    monkeypatch.setattr(city_land, "_into_the_citys_hands", billed_first)

    async with factory() as db, db.begin():
        house = await db.get(Node, home_id)
        assert house is not None
        await town.cede(db, await db.get(Body, body_id), house)
    (listed,) = await asyncio.gather(*runs)

    async with factory() as db:
        written = (
            (
                await db.execute(
                    select(Event).where(
                        Event.kind == EventKind.UTILITY_CUT_OFF, Event.node_id == home_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert not written, "the holder's debt was written off by the hand-over"
    assert waited == [True], "the run waited for the cede's meter"
    assert listed == 1
    holder, debt, cut_off = await _held_by(factory, home_id, meter_id)
    assert holder is None and debt == 0 and not cut_off
    assert (await _meter(factory, meter_id)).counted_at == moment


async def test_locations_taken_back_together_take_their_meters_in_the_runs_order(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deploy's catch-up takes back every city location handed out as a
    plot in one transaction (`reclaim_all`), and a meter run takes every meter
    in id order: the catch-up must take the meters it needs in that order too,
    all of them before the first location.

    Two locations whose meters sort the other way round from the order the
    city lists its land. The catch-up has taken the first one back -- holding
    its meter -- when the run starts. When each location took its own meter as
    it came, the run took the second one's meter and waited for the first's,
    and the catch-up then reached for the second's. Now the catch-up holds both
    before it starts; the run waits, and bills the city after it.
    """
    moment = datetime.now(UTC)
    hours = constants[R.ENERGY_METER_PERIOD]
    ((centre, home),) = await _grids(session, constants, catalog, 1)
    owner, _ = await _resident(session, home, "Захвативший")
    market = await world.create_node(
        session, f"{home.key}.market", "Рынок", area_m2=100, parent=centre
    )
    market.owner_city_id = home.owner_city_id
    for location in (home, market):
        location.owner_identity_id = owner.id
        location.properties = {}
    await session.flush()
    city = await session.get(City, home.owner_city_id)
    assert city is not None
    listed = [one for one in await town.territory(session, city) if one in (home, market)]
    #: The first location the territory lists gets the meter that sorts last.
    meters = await _open(
        session, constants, list(reversed(listed)), since=moment - timedelta(hours=hours)
    )
    ids = ([one.id for one in listed], [meter.id for meter in meters])
    await session.commit()
    node_ids, meter_ids = ids

    async def run() -> int:
        async with factory() as db, db.begin():
            return await utility.run_meters(db, constants, now=moment)

    waited: list[bool] = []
    runs: list[asyncio.Future[int]] = []
    taken_back = city_land.reclaim

    async def run_after_the_first(db, *args, **kwargs):
        result = await taken_back(db, *args, **kwargs)
        if result and not runs:
            runs.append(asyncio.ensure_future(run()))
            waited.append(await _until_blocked_by(factory, db, unless=runs[0]))
        return result

    monkeypatch.setattr(city_land, "reclaim", run_after_the_first)

    async with factory() as db, db.begin():
        taken = await town.reclaim_all(db)
    (walked,) = await asyncio.gather(*runs)

    assert [node.id for _, node in taken] == node_ids
    assert waited == [True], "the run waited for the catch-up's meters"
    assert walked == 2
    for node_id, meter_id in zip(node_ids, meter_ids[::-1], strict=True):
        holder, debt, cut_off = await _held_by(factory, node_id, meter_id)
        assert holder is None and debt == 0 and not cut_off
        assert (await _meter(factory, meter_id)).counted_at == moment


async def test_a_plot_ceded_as_the_run_starts_is_billed_to_the_city(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run bills whoever holds a node once it has the meters, not whoever
    held it when the run first read the node.

    The run opens the missing meters first, and reads every held node to do
    it. A holder without a coin gives the plot up just then -- before the run
    takes the meters, so nothing waits. The run's session still has the node
    as it read it. When the run billed that row, it wrote the holder's debt
    onto a plot the city held; now it reads the nodes again after the meters'
    lock and bills the treasury in energy.
    """
    moment = datetime.now(UTC)
    hours = constants[R.ENERGY_METER_PERIOD]
    ((grid, home),) = await _grids(session, constants, catalog, 1)
    owner, body = await _resident(session, home, "Хозяин")
    home.owner_identity_id = owner.id
    (meter,) = await _open(session, constants, [home], since=moment - timedelta(hours=hours))
    ids = (body.id, home.id, grid.id, meter.id)
    await session.commit()
    body_id, home_id, grid_id, meter_id = ids

    opened = utility.ensure_meters
    #: Held on purpose, as a local further up the run would hold it: the
    #: session keeps its rows weakly, and the run must not rely on nobody
    #: holding the row it read.
    stale: list[Node | None] = []

    async def ceded_after(db, *args, **kwargs):
        result = await opened(db, *args, **kwargs)
        stale.append(await db.get(Node, home_id))
        async with factory() as elsewhere, elsewhere.begin():
            house = await elsewhere.get(Node, home_id)
            assert house is not None
            await town.cede(elsewhere, await elsewhere.get(Body, body_id), house)
        return result

    monkeypatch.setattr(utility, "ensure_meters", ceded_after)

    async with factory() as db, db.begin():
        assert await utility.run_meters(db, constants, now=moment) == 1

    assert stale and stale[0] is not None
    holder, debt, cut_off = await _held_by(factory, home_id, meter_id)
    assert holder is None
    assert debt == 0 and not cut_off, "the former holder's debt on the city's plot"
    assert (await _meter(factory, meter_id)).counted_at == moment
    drawn = 100_000 - await _pool_left(factory, constants, grid_id)
    assert drawn == pytest.approx(utility.draw_for(constants, home, hours), abs=0.01)
