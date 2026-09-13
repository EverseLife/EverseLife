# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over the automats' tick.

One of the race files (see `test_races.py` for the family's method): here the
contended things are the ones the tick holds for the whole world at once -- the
yards' stacks of every factory and the city pools they draw (D-253, D-135) --
against a player who takes a stack and a pool in one command: a master at a
powered machine (D-269), or the owner reprogramming a machine -- and the
owner's purse, which the tick bills only after the machines have worked.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import IRON, NAILS, _factory_floor, _learn, _lube_in
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import automat, craft, energy, ledger, stock, world
from src.engine.automat import run as automat_run
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
    -- and only then the pool (`craft/batch/work.py`). The handshake starts the
    tick while the master provably holds those stacks: the tick works the
    assembler and reaches for the sand, the master reaches for the pool. When
    the assembler's advance drew the pool on the spot, the tick held it while
    it waited for the master, the master waited for it, and Postgres killed
    one of the two. Now the bench gets the pool, commits, and the tick goes on.
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
        #: `conftest._slow` with a signal: the master's stacks are taken, and
        #: the pause keeps them taken while the tick walks into them.
        rows = await locked(*args, **kwargs)
        held.set()
        await asyncio.sleep(0.5)
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
        again = await db.get(Node, node_id)
        assert again is not None
        after = await energy.pool_of(db, constants, again, create=False)
        assert after is not None
        #: The pool paid the bench, and both machines' hours at the end of the tick.
        assert stored - float(after.stored) == pytest.approx(2 * 2 * rate + plan.energy, abs=0.01)


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

    The handshake commits the spending at the very edge: after every machine
    worked, before the first draw. The pass goes back and runs again without
    that owner: nothing made, nothing drunk, the hours waiting by the clock.
    Another owner's factory beside it works in the same step all the same.
    """
    _, yard, identity, body, machine = await _factory_floor(session, constants)
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    lube = await _lube_in(session, yard, 100)
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)

    _, other_yard, other, other_body, other_machine = await _factory_floor(session, constants)
    await world.grant_item(session, other_yard, IRON, amount=1000, quality=60, origin="test")
    await _lube_in(session, other_yard, 100)
    await _learn(session, other, NAILS)
    neighbour = await automat.program(session, constants, catalog, other_body, other_machine, NAILS)

    started = row.counted_at
    moment = started + timedelta(hours=2)
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    ids = (row.id, neighbour.id, account.id, yard.id, other_yard.id, lube.id)
    await session.commit()
    row_id, neighbour_id, account_id, yard_id, other_yard_id, lube_id = ids

    drawn = automat_run._pay
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

    monkeypatch.setattr(automat_run, "_pay", spent_first)

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
