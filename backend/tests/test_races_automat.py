# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over the automats' tick.

One of the race files (see `test_races.py` for the family's method): here the
contended things are the ones the tick holds for the whole world at once -- the
yards' stacks of every factory and the city pools they draw (D-253, D-135) --
against a player who takes a stack, a pool and a purse in one command: a
master at a powered machine (D-269), or the owner reprogramming a machine --
and the owner's purse and the pool, which the tick draws only after the
machines have worked.

The handshake is `_until_blocked_by`: the side holding the contended rows
lets go only once the other side has provably walked into them.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import IRON, NAILS, _factory_floor, _learn, _lube_in
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import automat, battery, craft, energy, ledger, stock, utility, world
from src.engine.automat import bill as energy_bill
from src.engine.automat import run as automat_run
from src.models.automat import Automat as AutomatRow
from src.models.city import UtilityMeter
from src.models.craft import CraftBatch
from src.models.identity import Body, Identity
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import ABOARD, Layer, Node
from src.units import amount, amount_float, money

FURNACE = "blast_furnace"
SILICON = "silicon"
SAND = "quartz_sand"
COKE = "petroleum_coke"

_BLOCKED = text("SELECT count(*) FROM pg_stat_activity WHERE :holder = ANY(pg_blocking_pids(pid))")


async def _until_blocked_by(
    factory: async_sessionmaker[AsyncSession], holder: AsyncSession
) -> None:
    """Return once another transaction waits on a lock `holder` holds.

    A fixed pause would let a busy run release the held rows before the other
    side reached them, and the race would pass on the very code it exists to
    catch. Asked by the holder's own backend, so no unrelated wait in the
    database counts; the activity view is a snapshot per transaction, so each
    look is a transaction of its own.
    """
    pid = (await holder.execute(text("SELECT pg_backend_pid()"))).scalar_one()
    async with factory() as probe:
        for _ in range(500):
            blocked = (await probe.execute(_BLOCKED, {"holder": pid})).scalar_one()
            await probe.rollback()
            if blocked:
                return
            await asyncio.sleep(0.01)
    raise AssertionError("nobody came to wait on the held rows")


async def _nails_on(db: AsyncSession, yard_id) -> list[Item]:
    stmt = select(Item).where(Item.container_id == yard_id, Item.type_key == NAILS)
    return list((await db.execute(stmt.execution_options(populate_existing=True))).scalars())


async def _take_a_nail(factory: async_sessionmaker[AsyncSession], yard_id) -> None:
    """A player takes one nail off the yard's stack, committed on the spot."""
    async with factory() as hand, hand.begin():
        #: A lock the tick still held would hang here: fail fast instead.
        await hand.execute(text("SET LOCAL lock_timeout = '3s'"))
        (stack,) = await _nails_on(hand, yard_id)
        locked = await hand.get(Item, stack.id, with_for_update=True)
        assert locked is not None and locked.amount > amount(1)
        locked.amount -= amount(1)


async def _pool_left(factory: async_sessionmaker[AsyncSession], constants, node_id) -> float:
    async with factory() as db:
        node = await db.get(Node, node_id)
        assert node is not None
        pool = await energy.pool_of(db, constants, node, create=False)
        assert pool is not None
        return float(pool.stored)


async def test_a_crafter_drawing_the_pool_does_not_deadlock_the_automats_tick(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tick holds every machine's stacks until it commits; the pool it
    must take last, or it deadlocks with a bench.

    Two machines on one floor, wired so the tick advances the assembler first
    and the furnace automat second. A master at the blast furnace standing on
    the same floor takes the sand and the coke -- the furnace automat's inputs
    -- and only then the pool (`craft/batch/work.py`). The master holds those
    stacks until the tick has walked into them: the tick works the assembler
    and waits for the sand, the master reaches for the pool. When the
    assembler's advance drew the pool on the spot, the tick held it while it
    waited for the master, the master waited for it, and Postgres killed one
    of the two. Now the bench gets the pool, commits, and the tick goes on.
    """
    node, yard, identity, body, assembler = await _factory_floor(session, constants)
    smelter = await world.grant_item(session, yard, "auto_furnace", quality=70, origin="test")
    await world.grant_item(session, yard, FURNACE, quality=60, origin="test")
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    await world.grant_item(session, yard, SAND, amount=400, quality=60, origin="test")
    await world.grant_item(session, yard, COKE, amount=200, quality=60, origin="test")
    await _lube_in(session, yard, 1000)
    await _learn(session, identity, NAILS)
    await _learn(session, identity, SILICON)
    nails_row = await automat.program(session, constants, catalog, body, assembler, NAILS)
    silicon_row = await automat.program(session, constants, catalog, body, smelter, SILICON)
    #: The wire sets the tick's order whatever the uuids: nails, then silicon.
    await automat.link(session, body, assembler, smelter)
    moment = nails_row.counted_at + timedelta(hours=2)
    plan = await craft.plan(session, constants, catalog, body, SILICON, 2)
    assert plan.energy > 0, "the blast furnace runs on the pool (D-269)"
    pool = await energy.pool_of(session, constants, node)
    assert pool is not None
    stored = float(pool.stored)
    ids = (body.id, node.id, nails_row.id, silicon_row.id)
    await session.commit()
    body_id, node_id, nails_id, silicon_id = ids

    held = asyncio.Event()
    locked = stock.lock_items

    async def holding(db, *args, **kwargs):
        rows = await locked(db, *args, **kwargs)
        held.set()
        await _until_blocked_by(factory, db)
        return rows

    monkeypatch.setattr(stock, "lock_items", holding)

    async def bench() -> CraftBatch:
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            assert me is not None
            return await craft.start(db, constants, catalog, me, SILICON, 2)

    async def tick() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            return await automat.tick_automats(db, constants, now=moment)

    batch, made = await asyncio.gather(bench(), tick())

    assert batch.output == SILICON
    assert made > 0
    rate = constants[R.AUTO_ENERGY_PER_HOUR]
    async with factory() as db:
        rows = {
            row.id: row
            for row in (
                await db.execute(
                    select(AutomatRow).where(AutomatRow.id.in_((nails_id, silicon_id)))
                )
            ).scalars()
        }
        #: Both machines worked their hours -- neither was the deadlock's
        #: victim, passed over by its savepoint and left for the next tick.
        assert rows[nails_id].counted_at == moment
        assert rows[silicon_id].counted_at == moment
    #: The pool paid the bench, and both machines' hours at the end of the tick.
    left = await _pool_left(factory, constants, node_id)
    assert stored - left == pytest.approx(2 * 2 * rate + plan.energy, abs=0.01)


async def test_a_bench_holding_a_later_pool_does_not_deadlock_the_tick_on_its_purse(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tick takes every pool before any purse; a bench takes its pool and
    then its master's purse (`energy.draw_for_work`).

    Two cities. The owner's machine draws the pool that sorts first; the owner
    stands in the other city at a blast furnace, holding that city's pool, and
    reaches for the purse only once the tick has come to wait for the pool --
    the tick needs it for the other city's machine. When the tick paid each
    bill as it drew it, it held the owner's purse by then, and the two waited
    on each other. Now it holds only the first pool; the bench pays, commits,
    and the tick goes on.
    """
    one = await _factory_floor(session, constants)
    two = await _factory_floor(session, constants)
    grids = {}
    for floor in (one, two):
        pool = await energy.pool_of(session, constants, floor[0])
        assert pool is not None
        grids[pool.node_id] = floor
    (first_node, first_yard, owner, owner_body, first_machine) = grids[min(grids)]
    (second_node, second_yard, other, other_body, second_machine) = grids[max(grids)]
    for yard, who, body, machine in (
        (first_yard, owner, owner_body, first_machine),
        (second_yard, other, other_body, second_machine),
    ):
        await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
        await _lube_in(session, yard, 1000)
        await _learn(session, who, NAILS)
        await automat.program(session, constants, catalog, body, machine, NAILS)
    rows = (await session.execute(select(AutomatRow))).scalars().all()
    moment = min(row.counted_at for row in rows) + timedelta(hours=2)

    #: The owner walks to the second city and stands at its blast furnace
    #: with the sand and the coke in the pocket.
    await world.grant_item(session, second_yard, FURNACE, quality=60, origin="test")
    await _learn(session, owner, SILICON)
    owner_body.node_id = second_node.id
    pocket = await world.body_container(session, owner_body)
    await world.grant_item(session, pocket, SAND, amount=40, quality=60, origin="test")
    await world.grant_item(session, pocket, COKE, amount=20, quality=60, origin="test")
    await session.flush()
    plan = await craft.plan(session, constants, catalog, owner_body, SILICON, 2)
    assert plan.energy > 0
    first_before = float((await energy.pool_of(session, constants, first_node)).stored)
    second_before = float((await energy.pool_of(session, constants, second_node)).stored)
    ids = (owner_body.id, first_node.id, second_node.id, [row.id for row in rows])
    await session.commit()
    body_id, first_id, second_id, row_ids = ids

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

    async def bench() -> CraftBatch:
        async with factory() as db, db.begin():
            benches.append(db)
            me = await db.get(Body, body_id)
            assert me is not None
            return await craft.start(db, constants, catalog, me, SILICON, 2)

    async def tick() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            return await automat.tick_automats(db, constants, now=moment)

    batch, made = await asyncio.gather(bench(), tick())

    assert batch.output == SILICON
    assert made > 0
    async with factory() as db:
        for row_id in row_ids:
            row = await db.get(AutomatRow, row_id)
            assert row is not None and row.counted_at == moment
    rate = constants[R.AUTO_ENERGY_PER_HOUR]
    assert first_before - await _pool_left(factory, constants, first_id) == pytest.approx(
        2 * rate, abs=0.01
    )
    assert second_before - await _pool_left(factory, constants, second_id) == pytest.approx(
        2 * rate + plan.energy, abs=0.01
    )


async def test_the_tick_passes_over_a_machine_its_owner_holds(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A machine whose row another transaction holds -- its owner mid-command --
    is skipped for this tick rather than waited for: the tick already holds the
    stacks of the machines before it, and the command may be waiting for one
    of them. The rest of the floor works, and the held machine's hours wait by
    the clock for the next tick."""
    _, yard, identity, body, assembler = await _factory_floor(session, constants)
    smelter = await world.grant_item(session, yard, "auto_furnace", quality=70, origin="test")
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    await world.grant_item(session, yard, "iron_ore", amount=1000, quality=60, origin="test")
    await world.grant_item(session, yard, "coal", amount=1000, quality=60, origin="test")
    await _lube_in(session, yard, 1000)
    await _learn(session, identity, NAILS)
    free = await automat.program(session, constants, catalog, body, assembler, NAILS)
    busy = await automat.program(session, constants, catalog, body, smelter, IRON)
    started = busy.counted_at
    moment = free.counted_at + timedelta(hours=2)
    free_id, busy_id = free.id, busy.id
    await session.commit()

    async with factory() as owner, owner.begin():
        await owner.execute(select(AutomatRow).where(AutomatRow.id == busy_id).with_for_update())

        async def tick() -> float:
            async with factory() as db, db.begin():
                return await automat.tick_automats(db, constants, now=moment)

        #: Waiting for the owner would hang here until the owner lets go.
        made = await asyncio.wait_for(tick(), timeout=5)

    assert made > 0
    async with factory() as db:
        worked = await db.get(AutomatRow, free_id)
        waited = await db.get(AutomatRow, busy_id)
        assert worked is not None and waited is not None
        assert worked.counted_at == moment
        assert waited.counted_at == started


async def test_a_purse_emptied_under_the_tick_buys_no_free_hours(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whoever cannot pay does not burn (D-135), and the tick bills a machine
    only after it has worked. A purse the forecast found full and another
    transaction emptied before the draw -- the owner's own bench or market
    order committing mid-step -- must not leave the goods standing unpaid.

    The spending commits at the very edge: after every machine worked, before
    the first draw. The pass goes back and runs again with that purse empty:
    nothing made, nothing drunk, and the hours gone -- as when the forecast
    itself finds the purse short, so no owner banks hours by moving money away
    before each draw. Another owner's factory beside it works in the same step
    all the same -- its pool, written and rolled back by the first run, read
    afresh by the second.
    """
    _, yard, identity, body, machine = await _factory_floor(session, constants)
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    lube = await _lube_in(session, yard, 100)
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)

    other_node, other_yard, other, other_body, other_machine = await _factory_floor(
        session, constants
    )
    await world.grant_item(session, other_yard, IRON, amount=1000, quality=60, origin="test")
    await _lube_in(session, other_yard, 100)
    await _learn(session, other, NAILS)
    neighbour = await automat.program(session, constants, catalog, other_body, other_machine, NAILS)

    started = row.counted_at
    moment = started + timedelta(hours=2)
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    other_pool = await energy.pool_of(session, constants, other_node)
    assert other_pool is not None
    other_before = float(other_pool.stored)
    ids = (row.id, neighbour.id, account.id, yard.id, other_yard.id, lube.id, other_node.id)
    await session.commit()
    row_id, neighbour_id, account_id, yard_id, other_yard_id, lube_id, other_node_id = ids

    drawn = energy_bill.pay
    spent: list[int] = []

    async def spent_first(*args, **kwargs):
        if not spent:
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
            spent.append(purse)
        return await drawn(*args, **kwargs)

    monkeypatch.setattr(energy_bill, "pay", spent_first)

    async with factory() as db, db.begin():
        made = await automat.tick_automats(db, constants, now=moment)

    assert spent, "the purse was emptied between the forecast and the draw"
    assert made > 0, "the neighbour's factory worked in the same step"
    async with factory() as db:
        mine = await db.get(AutomatRow, row_id)
        theirs = await db.get(AutomatRow, neighbour_id)
        assert mine is not None and theirs is not None
        assert mine.counted_at == moment, "the unpaid hours are gone, not banked for later"
        assert theirs.counted_at == moment

        def nails_in(container_id):
            return select(Item).where(Item.container_id == container_id, Item.type_key == NAILS)

        assert not (await db.execute(nails_in(yard_id))).scalars().all(), "nothing made free"
        assert (await db.execute(nails_in(other_yard_id))).scalars().all()
        oil = await db.get(Item, lube_id)
        assert oil is not None and amount_float(oil.amount) == pytest.approx(100)
        assert await ledger.balance(db, account_id) == 0
    #: The neighbour's hours were drawn once, by the second run, not twice.
    rate = constants[R.AUTO_ENERGY_PER_HOUR]
    assert other_before - await _pool_left(factory, constants, other_node_id) == pytest.approx(
        2 * rate, abs=0.01
    )


async def test_a_pool_drunk_under_the_tick_is_billed_for_what_it_gave(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The supply is not held to the forecast, the purse is: a pool drunk dry
    between the forecast and the draw -- a crafter on the same grid committing
    mid-step -- leaves the machine's hours worked, the pool at nought and not
    below, and the owner billed for nothing the pool did not give. A building
    finishes what it began on an emptied pool."""
    node, yard, identity, body, machine = await _factory_floor(session, constants)
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    await _lube_in(session, yard, 100)
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)
    moment = row.counted_at + timedelta(hours=2)
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    purse = await ledger.balance(session, account.id)
    ids = (row.id, node.id, account.id)
    await session.commit()
    row_id, node_id, account_id = ids

    drawn = energy_bill.pay
    drunk: list[bool] = []

    async def drunk_first(*args, **kwargs):
        if not drunk:
            async with factory() as elsewhere, elsewhere.begin():
                here = await elsewhere.get(Node, node_id)
                assert here is not None
                pool = await energy.pool_of(elsewhere, constants, here, lock=True)
                assert pool is not None
                energy.take_from_pool(pool, float(pool.stored))
            drunk.append(True)
        return await drawn(*args, **kwargs)

    monkeypatch.setattr(energy_bill, "pay", drunk_first)

    async with factory() as db, db.begin():
        made = await automat.tick_automats(db, constants, now=moment)

    assert drunk and made > 0, "the hours were worked"
    async with factory() as db:
        worked = await db.get(AutomatRow, row_id)
        assert worked is not None and worked.counted_at == moment
        assert await ledger.balance(db, account_id) == purse, "nothing billed for nothing given"
    assert await _pool_left(factory, constants, node_id) == pytest.approx(0, abs=0.001)


async def test_the_off_grid_tick_and_the_automats_tick_take_hulls_cells_in_one_order(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two passes take several hulls' cells in one transaction: the off-grid
    tick charging them from the panels (`battery.tick_offgrid`), and the
    automats' tick drawing them at its end (`bill.pay`). Both go hull by hull
    (`battery.hull_of`) -- and they run as two jobs of one tick at once.

    Two hulls whose rooms sort the other way round from the hulls themselves.
    The off-grid tick holds its first hull's cells until the automats' tick
    has come to wait for them. When it went room by room, that first hull was
    the automats' second: each held the hull the other wanted next.
    """
    moment = datetime.now(UTC)
    stamp = uuid.uuid4().hex[:8]
    hulls = sorted(
        [
            await world.create_node(
                session, f"hull.{stamp}.{n}", "Hull", area_m2=100, layer=Layer.SPACE
            )
            for n in range(2)
        ],
        key=lambda node: node.id,
    )
    rooms = sorted(
        [
            await world.create_node(
                session,
                f"hull.{stamp}.room{n}",
                "Room",
                area_m2=50,
                layer=Layer.LOCATION,
                properties={ABOARD: True},
            )
            for n in range(2)
        ],
        key=lambda node: node.id,
    )
    #: The lower room to the higher hull: room order and hull order disagree.
    rooms[0].parent_id, rooms[1].parent_id = hulls[1].id, hulls[0].id
    solar = world.station_names(battery.SOLAR)[0]
    row_ids = []
    for room in rooms:
        yard = await world.node_container(session, room)
        panel = await world.grant_item(session, yard, solar, quality=60, origin="test")
        panel.charged_at = moment - timedelta(hours=1)
        cell = await world.grant_item(session, yard, "battery", quality=60, origin="test")
        cell.charge = Decimal(str(battery.capacity(constants) / 2))
        cell.charged_at = moment
        machine = await world.grant_item(session, yard, "auto_station", quality=70, origin="test")
        await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
        await _lube_in(session, yard, 100)
        #: Put straight in: a programme is an owner's command, and the race is
        #: the two ticks'. No owner, no bill -- the cells are the whole draw.
        row = AutomatRow(
            item_id=machine.id,
            node_id=room.id,
            owner_identity_id=None,
            recipe_key=NAILS,
            backlog=Decimal(0),
            counted_at=moment - timedelta(hours=2),
        )
        session.add(row)
        await session.flush()
        row_ids.append(row.id)
    await session.commit()

    held = asyncio.Event()
    offgrid: list[AsyncSession] = []
    gathered = battery.batteries_in

    async def holding(db, *args, **kwargs):
        cells = await gathered(db, *args, **kwargs)
        if offgrid and db is offgrid[0] and not held.is_set():
            held.set()
            await _until_blocked_by(factory, db)
        return cells

    monkeypatch.setattr(battery, "batteries_in", holding)

    async def charge() -> float:
        async with factory() as db, db.begin():
            offgrid.append(db)
            return await energy.tick_offgrid(db, constants, now=moment)

    async def tick() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            return await automat.tick_automats(db, constants, now=moment)

    banked, made = await asyncio.gather(charge(), tick())

    assert banked > 0 and made > 0
    async with factory() as db:
        for row_id in row_ids:
            worked = await db.get(AutomatRow, row_id)
            assert worked is not None and worked.counted_at == moment


async def test_a_stack_taken_after_a_machine_rolled_back_is_not_counted_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A machine that fails goes back to its savepoint, and the rollback puts
    back the nails stack its payout had merged and deleted -- with the amount it
    had then, while the lock on it is already gone. A player takes a nail from
    it before the next machine lands its own nails there: the merge must add
    what is left, not what was there.

    The session keeps its rows weakly, so the stale stack survives the rollback
    only while something holds it -- a memo, a local further up. The test holds
    it on purpose: the tick must not rely on nobody doing so."""
    _, yard, identity, body, first = await _factory_floor(session, constants)
    #: A different quality, so the two machines stand as two things.
    second = await world.grant_item(session, yard, "auto_station", quality=71, origin="test")
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    await _lube_in(session, yard, 1000)
    await _learn(session, identity, NAILS)
    breaking = await automat.program(session, constants, catalog, body, first, NAILS)
    landing = await automat.program(session, constants, catalog, body, second, NAILS)
    await automat.link(session, body, first, second)
    earlier = breaking.counted_at + timedelta(hours=10)
    ids = (yard.id, breaking.id, landing.id)
    await session.commit()
    yard_id, breaking_id, landing_id = ids

    async with factory() as db, db.begin():
        await automat.tick_automats(db, constants, now=earlier)
    async with factory() as db:
        (stack,) = await _nails_on(db, yard_id)
        before = amount_float(stack.amount)
    assert before > 1

    real = automat_run.advance
    paid: list[float] = []
    held: list[Item] = []

    async def failing(*args, **kwargs):
        if args[2].id == landing_id:
            await _take_a_nail(factory, yard_id)
            paid.append(await real(*args, **kwargs))
            return paid[-1]
        held.extend(await _nails_on(args[0], yard_id))
        made = await real(*args, **kwargs)
        if args[2].id == breaking_id:
            raise RuntimeError("a programme the vault has since broken")
        return made

    monkeypatch.setattr(automat_run, "advance", failing)

    async with factory() as db, db.begin():
        await automat.tick_automats(db, constants, now=earlier + timedelta(hours=10))

    assert paid and paid[0] > 0
    async with factory() as db:
        total = sum(amount_float(one.amount) for one in await _nails_on(db, yard_id))
    assert total == pytest.approx(before - 1 + paid[0]), "the nail taken is not given back"


async def test_a_stack_taken_between_two_runs_of_a_pass_is_not_counted_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same hole one level up: a purse that moved sends the whole pass
    back, and the rollback puts the neighbour's merged nails stack back as it
    was. A player takes a nail before the second run lands the neighbour's
    nails there again: the merge must add what is left."""
    _, yard, identity, body, machine = await _factory_floor(session, constants)
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    await _lube_in(session, yard, 1000)
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)
    _, other_yard, other, other_body, other_machine = await _factory_floor(session, constants)
    await world.grant_item(session, other_yard, IRON, amount=1000, quality=60, origin="test")
    await _lube_in(session, other_yard, 1000)
    await _learn(session, other, NAILS)
    await automat.program(session, constants, catalog, other_body, other_machine, NAILS)
    earlier = row.counted_at + timedelta(hours=10)
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    ids = (other_yard.id, account.id)
    await session.commit()
    other_yard_id, account_id = ids

    async with factory() as db, db.begin():
        await automat.tick_automats(db, constants, now=earlier)
    async with factory() as db:
        (stack,) = await _nails_on(db, other_yard_id)
        before = amount_float(stack.amount)
    assert before > 1

    drawn = energy_bill.pay
    passed = automat_run._pass
    runs: list[int] = []

    async def spent_first(*args, **kwargs):
        if len(runs) == 1:
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
        return await drawn(*args, **kwargs)

    held: list[Item] = []

    async def counted(*args, **kwargs):
        runs.append(1)
        if len(runs) == 1:
            #: Held across the rollback, as in the machine-level race above.
            held.extend(await _nails_on(args[0], other_yard_id))
        if len(runs) == 2:
            await _take_a_nail(factory, other_yard_id)
        return await passed(*args, **kwargs)

    monkeypatch.setattr(energy_bill, "pay", spent_first)
    monkeypatch.setattr(automat_run, "_pass", counted)

    async with factory() as db, db.begin():
        made = await automat.tick_automats(db, constants, now=earlier + timedelta(hours=10))

    assert len(runs) == 2, "the moved purse sent the pass back once"
    assert made > 0
    async with factory() as db:
        total = sum(amount_float(one.amount) for one in await _nails_on(db, other_yard_id))
    assert total == pytest.approx(before - 1 + made), "the nail taken is not given back"


async def test_an_owner_pays_the_debt_while_the_tick_stands_the_cut_off_machine(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The tick asks a machine's meter whether the node is cut off (D-149) and
    keeps its transaction open for the rest of the world's factories; what this
    pins is that the reading leaves no lock on the meter's row behind. The owner
    paying the debt meanwhile -- locking the purse, then writing the meter --
    walks straight through instead of waiting for the step to end. A meter held
    that long would put it on the tick's lock order, ahead of the purses the
    step reaches for last (`bill.pay`), where a payer holding a purse and
    wanting the meter is that order the other way round."""
    node, yard, identity, body, machine = await _factory_floor(session, constants)
    node.owner_identity_id = identity.id
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    await _lube_in(session, yard, 100)
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)
    meter = await utility.meter_of(session, node)
    assert meter is not None
    meter.cut_off, meter.debt = True, money(1)
    moment = row.counted_at + timedelta(hours=2)
    ids = (row.id, meter.id, node.id, identity.id)
    await session.commit()
    row_id, meter_id, node_id, identity_id = ids

    async with factory() as tick, tick.begin():
        assert await automat.tick_automats(tick, constants, now=moment) == 0
        async with factory() as owner, owner.begin():
            #: A tick holding the meter makes this fail at once, not hang.
            await owner.execute(text("SET LOCAL lock_timeout = '2s'"))
            payer = await owner.get(Identity, identity_id)
            where = await owner.get(Node, node_id)
            assert payer is not None and where is not None
            assert await utility.pay(owner, constants, payer, where) == money(1)

    async with factory() as db:
        paid = await db.get(UtilityMeter, meter_id)
        stood = await db.get(AutomatRow, row_id)
        assert paid is not None and not paid.cut_off and paid.debt == 0
        assert stood is not None and stood.counted_at == moment
