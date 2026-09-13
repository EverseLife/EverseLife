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

The handshake is `_until_one_waits`: the side holding the contended rows lets
go only once the other side has provably walked into them.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import IRON, NAILS, _factory_floor, _learn, _lube_in
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import automat, craft, energy, ledger, stock, world
from src.engine.automat import bill as energy_bill
from src.models.automat import Automat as AutomatRow
from src.models.craft import CraftBatch
from src.models.identity import Body
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Node
from src.units import amount_float

FURNACE = "blast_furnace"
SILICON = "silicon"
SAND = "quartz_sand"
COKE = "petroleum_coke"

_WAITING = text(
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
)


async def _until_one_waits(factory: async_sessionmaker[AsyncSession]) -> None:
    """Return once a transaction of this database waits on a lock.

    A fixed pause would let a busy run release the held rows before the other
    side reached them, and the race would pass on the very code it exists to
    catch. The activity view is a snapshot per transaction, so each look is a
    transaction of its own.
    """
    async with factory() as probe:
        for _ in range(500):
            waiting = (await probe.execute(_WAITING)).scalar_one()
            await probe.rollback()
            if waiting:
                return
            await asyncio.sleep(0.01)
    raise AssertionError("nobody came to wait on the held rows")


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

    async def holding(*args, **kwargs):
        rows = await locked(*args, **kwargs)
        held.set()
        await _until_one_waits(factory)
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
            await _until_one_waits(factory)
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
    the first draw. The pass goes back and runs again without that owner:
    nothing made, nothing drunk, the hours waiting by the clock. Another
    owner's factory beside it works in the same step all the same -- its pool,
    written and rolled back by the first run, read afresh by the second.
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
        assert mine.counted_at == started, "the unpaid machine's hours wait for the next tick"
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
