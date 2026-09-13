# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over a city pool and the stacks a draw takes first.

One of the race files (see `test_races.py` for the family's method). Everything
that draws a pool brings it up to now first (`energy.produce`), and a fuel
plant burns the pile it stands on (D-082) -- so a draw takes the fuel of the
city's plants along with its pool: the energy step, a bench at a powered
machine (D-269), a body print, the automats' tick (D-253). Those stacks come
before the pool, like every stack a draw needs; the races here are the
automats' tick holding such stacks from its advance -- the plant's pile it
poured its coke into, the iron by a printer -- against each of the others.

A cell charged from the pool (`battery.charge_battery`) is the other kind of
race here: the pool comes first and the cell after it, and where the cell lies
is asked of its row once that lock is had. A cell lifted off the floor while
the charge waited on it is in the lifter's hands, and one burnt with its yard
(`plates._burn`) is nowhere; neither is charged, nor billed to the charger.

The handshake is `_until_blocked_by`: the side holding the contended rows
lets go only once the other side has provably walked into them.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import IRON, NAILS, _factory_floor, _learn, _lube_in, _pool_left, _until_blocked_by
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import (
    automat,
    battery,
    craft,
    death,
    energy,
    ledger,
    liquid,
    plates,
    storage,
    world,
)
from src.engine.craft import procedure
from src.models.automat import Automat as AutomatRow
from src.models.craft import CraftBatch
from src.models.energy import EnergyPool
from src.models.identity import Body, Identity
from src.models.inventory import Item
from src.models.job import Job, JobKind
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node
from src.units import amount_float, money

FURNACE = "blast_furnace"
SILICON = "silicon"
SAND = "quartz_sand"
COKE = "petroleum_coke"
PLANT = "coal_plant"
OIL = "crude_oil"
#: Energy the pool holds for a charge: more than a cell takes, so the cell's
#: room decides the pour and the bill.
POOL_ENERGY = 1000


def _hold_the_first(
    monkeypatch: pytest.MonkeyPatch,
    factory: async_sessionmaker[AsyncSession],
    module: object,
    name: str,
) -> asyncio.Event:
    """The first call of `module.name` holds the rows it locked until another
    transaction waits on them. The event says they are held; the session is
    the call's first argument, as it is for every engine door."""
    held = asyncio.Event()
    locked = getattr(module, name)

    async def holding(*args, **kwargs):
        rows = await locked(*args, **kwargs)
        if not held.is_set():
            held.set()
            await _until_blocked_by(factory, args[0])
        return rows

    monkeypatch.setattr(module, name, holding)
    return held


async def _coker_by_the_plant(session: AsyncSession, constants: Constants, catalog: Catalog):
    """A coal plant burning a pile of coke, and a reactor automat beside it
    distilling more coke out of oil onto that very pile: it may not eat the
    plant's tank (D-342), but pouring into it is anybody's (D-189), and what
    it makes lands on the yard with the twins it folds into (D-214)."""
    node, yard, identity, body, reactor = await _factory_floor(
        session, constants, machine_kind="auto_reactor"
    )
    await world.grant_item(session, yard, PLANT, quality=60, origin="test")
    await world.grant_item(session, yard, COKE, amount=1000, quality=60, origin="test")
    barrel = await world.grant_item(session, yard, "canister", quality=60, origin="test")
    inside = await storage.inside(session, barrel)
    await world.grant_item(session, inside, OIL, amount=100, quality=60, origin="test")
    await _lube_in(session, yard, 1000)
    await _learn(session, identity, COKE)
    row = await automat.program(session, constants, catalog, body, reactor, COKE)
    pool = await energy.pool_of(session, constants, node)
    assert pool is not None
    #: The plant's hours are the reactor's: both are counted from one moment.
    pool.counted_at = row.counted_at
    await session.flush()
    return node, identity, body, row, pool


async def _counted(factory: async_sessionmaker[AsyncSession], row_id, pool_id):
    """When the machine and the pool were last brought up to now."""
    async with factory() as db:
        row = await db.get(AutomatRow, row_id)
        pool = await db.get(EnergyPool, pool_id)
        assert row is not None and pool is not None
        return row.counted_at, pool.counted_at


async def test_the_energy_step_does_not_deadlock_the_tick_on_a_fuel_plants_pile(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tick holds the pile's coke the reactor folded its output into
    until it commits, and draws the pool at the end (`bill.pay`); the energy
    step runs beside it (`tick.WORLD_STEPS`, the same stage) and burns that
    pile for the plant.

    When `produce` took the pile under the pool's lock, the step held the pool
    while it waited for the tick's coke, the tick reached for that pool, and
    Postgres killed one of the two. Now the step takes the pile first and waits
    for it holding no pool: the tick draws, commits, and lets it in -- and the
    plant's two hours are burned once, by whichever came first.
    """
    node, _, _, row, pool = await _coker_by_the_plant(session, constants, catalog)
    moment = row.counted_at + timedelta(hours=2)
    stored = float(pool.stored)
    ids = (node.id, row.id, pool.id)
    await session.commit()
    node_id, row_id, pool_id = ids

    held = _hold_the_first(monkeypatch, factory, world, "stack_up")

    async def tick() -> float:
        async with factory() as db, db.begin():
            return await automat.tick_automats(db, constants, now=moment)

    async def step() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            return await energy.tick_pools(db, constants, now=moment)

    made, _ = await asyncio.gather(tick(), step())

    assert made > 0
    assert await _counted(factory, row_id, pool_id) == (moment, moment)
    #: What the plant makes of the coke it burns (D-215), per hour.
    plant = constants[R.ENERGY_COAL_PLANT_FUEL_DRAW] * constants[R.ENERGY_FUEL_ENERGY][COKE]
    rate = constants[R.AUTO_ENERGY_PER_HOUR]
    assert await _pool_left(factory, constants, node_id) - stored == pytest.approx(
        2 * plant - 2 * rate, abs=0.01
    )


async def test_a_bench_on_the_grid_does_not_deadlock_the_tick_on_a_fuel_plants_pile(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A master at a blast furnace on the same floor draws the pool
    (`energy.draw_for_work`), and the draw brings the pool up to now -- the
    plant's coke with it. The master's own sand and coke are in the pocket:
    the pile is the plant's tank, not the bench's store (D-189, D-315).

    When the draw took the pool before the pile, the master held it waiting
    for the coke the tick folded its output into, while the tick waited for
    the pool. Now the master waits for the pile holding only the pocket, which
    the tick never touches.
    """
    node, identity, body, row, pool = await _coker_by_the_plant(session, constants, catalog)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, FURNACE, quality=60, origin="test")
    await _learn(session, identity, SILICON)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, SAND, amount=40, quality=60, origin="test")
    await world.grant_item(session, pocket, COKE, amount=20, quality=60, origin="test")
    plan = await craft.plan(session, constants, catalog, body, SILICON, 2)
    assert plan.energy > 0, "the blast furnace runs on the pool (D-269)"
    moment = row.counted_at + timedelta(hours=2)
    stored = float(pool.stored)
    ids = (node.id, body.id, row.id, pool.id)
    await session.commit()
    node_id, body_id, row_id, pool_id = ids

    held = _hold_the_first(monkeypatch, factory, world, "stack_up")

    async def tick() -> float:
        async with factory() as db, db.begin():
            return await automat.tick_automats(db, constants, now=moment)

    async def bench() -> CraftBatch:
        await held.wait()
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            assert me is not None
            return await craft.start(db, constants, catalog, me, SILICON, 2)

    made, batch = await asyncio.gather(tick(), bench())

    assert made > 0
    assert batch.output == SILICON
    assert await _counted(factory, row_id, pool_id) == (moment, moment)
    #: What the plant makes of the coke it burns (D-215), per hour.
    plant = constants[R.ENERGY_COAL_PLANT_FUEL_DRAW] * constants[R.ENERGY_FUEL_ENERGY][COKE]
    rate = constants[R.AUTO_ENERGY_PER_HOUR]
    assert await _pool_left(factory, constants, node_id) - stored == pytest.approx(
        2 * plant - 2 * rate - plan.energy, abs=0.01
    )


async def test_a_body_print_does_not_deadlock_the_tick_on_the_printers_iron(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A print takes the pool's energy and the iron lying by the printer
    (D-013); an assembler on the same floor makes nails of that iron.

    When the print brought the pool up to now and only then took the iron, it
    held the pool waiting for the tick's iron while the tick waited for the
    pool. Now the print takes the iron first, like every stack before its pool.
    """
    node, yard, identity, body, assembler = await _factory_floor(session, constants)
    await world.grant_item(session, yard, death.PRINTER, quality=60, origin="test")
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    await _lube_in(session, yard, 1000)
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, assembler, NAILS)
    pool = await energy.pool_of(session, constants, node)
    assert pool is not None
    pool.counted_at = row.counted_at
    moment = row.counted_at + timedelta(hours=2)
    stored = float(pool.stored)

    #: Somebody who died on this floor and pays for the print out of the purse.
    ghost, ghost_body = await world.spawn(session, f"Ghost-{uuid.uuid4().hex[:6]}", node)
    purse = await ledger.account_for(session, AccountKind.IDENTITY, ghost.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=genesis.id,
        credit=purse.id,
        amount=money(100_000),
        memo={},
    )
    await death.die(session, constants, ghost_body, cause="test")
    ids = (node.id, ghost.id, row.id, pool.id)
    await session.commit()
    node_id, ghost_id, row_id, pool_id = ids

    held = _hold_the_first(monkeypatch, factory, liquid, "locked_stacks")

    async def tick() -> float:
        async with factory() as db, db.begin():
            return await automat.tick_automats(db, constants, now=moment)

    async def printer() -> Job:
        await held.wait()
        async with factory() as db, db.begin():
            here = await db.get(Node, node_id)
            who = await db.get(Identity, ghost_id)
            assert here is not None and who is not None
            return await death.order(db, constants, catalog, who, here)

    made, job = await asyncio.gather(tick(), printer())

    assert made > 0
    assert job.kind == JobKind.BODY_PRINT
    assert await _counted(factory, row_id, pool_id) == (moment, moment)
    rate = constants[R.AUTO_ENERGY_PER_HOUR]
    assert stored - await _pool_left(factory, constants, node_id) == pytest.approx(
        2 * rate + constants[R.ENERGY_BODY_PRINT], abs=0.01
    )
    per_nail = procedure(catalog, NAILS).per_unit[IRON]
    async with factory() as db:
        iron = (await db.execute(select(Item).where(Item.type_key == IRON))).scalars().all()
        assert sum(amount_float(stack.amount) for stack in iron) == pytest.approx(
            1000 - constants[R.DEATH_IRON_COST] - per_nail * made, abs=0.01
        )


async def _cell_on_the_floor(session: AsyncSession, constants: Constants):
    """A city yard on the grid, an empty cell lying on its floor (D-278), a
    funded charger and a lifter beside it.

    The tariff is the vault's: a charge that goes through is paid for, so a
    charge into the wrong hands shows on the charger's purse. Returns the
    node, the charger, the lifter and the cell.
    """
    stamp = uuid.uuid4().hex[:8]
    city = await world.create_node(
        session, f"terra.cells.{stamp}", "City", area_m2=1, layer=Layer.PLANET
    )
    node = await world.create_node(
        session, f"terra.cells.{stamp}.yard", "Yard", area_m2=200, layer=Layer.PLANET, parent=city
    )
    pool = await energy.pool_of(session, constants, node)
    assert pool is not None
    assert pool.tariff > 0, "a charge into the wrong hands must show on a purse"
    pool.stored = Decimal(POOL_ENERGY)
    pool.counted_at = datetime.now(UTC)
    charger = await world.print_body(
        session, await world.create_identity(session, f"Charger-{stamp}"), node
    )
    lifter = await world.print_body(
        session, await world.create_identity(session, f"Lifter-{stamp}"), node
    )
    purse = await ledger.account_for(session, AccountKind.IDENTITY, charger.identity_id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=genesis.id,
        credit=purse.id,
        amount=money(1000),
        memo={},
    )
    cell = await world.grant_item(
        session,
        await world.node_container(session, node),
        battery.BATTERY,
        quality=60,
        origin="test",
        installed=False,
    )
    await session.flush()
    return node, charger, lifter, cell


async def _charging(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    charger_id: uuid.UUID,
    cell_id: uuid.UUID,
    held: asyncio.Event,
) -> float:
    """The charge that comes second: it sees the cell where it lay before the
    side holding its row lets go."""
    await held.wait()
    async with factory() as db, db.begin():
        me = await db.get(Body, charger_id)
        cell = await db.get(Item, cell_id)
        assert me is not None and cell is not None
        return await battery.charge_battery(db, constants, me, cell)


async def _purse_of(factory: async_sessionmaker[AsyncSession], identity_id: uuid.UUID) -> int:
    async with factory() as db:
        account = await ledger.account_for(db, AccountKind.IDENTITY, identity_id)
        return await ledger.balance(db, account.id)


async def test_a_cell_lifted_off_the_floor_is_not_charged_in_the_lifters_hands(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A charge asks where the cell lies of its row after the lock, not before.

    The lifter picks the cell up off the floor and keeps the transaction open,
    holding its row. The charger sees the cell still lying in the yard --
    within reach (D-179) -- brings the pool up to now and walks into the row.
    The lift commits. Judged by the sight from before the wait, the charge went
    on into a cell now in the lifter's hands: the city's energy poured into
    another body's pocket, and the charger billed for it. Judged after the
    wait, the cell is no longer here.
    """
    node, charger, lifter, cell = await _cell_on_the_floor(session, constants)
    node_id, charger_id, lifter_id, cell_id = node.id, charger.id, lifter.id, cell.id
    identity_id = charger.identity_id
    await session.commit()
    purse = await _purse_of(factory, identity_id)

    held = asyncio.Event()

    async def lift() -> float:
        async with factory() as db, db.begin():
            me = await db.get(Body, lifter_id)
            thing = await db.get(Item, cell_id)
            assert me is not None and thing is not None
            taken = await storage.pick(db, constants, catalog, me, thing)
            held.set()
            await _until_blocked_by(factory, db)
            return taken

    lifted, charged = await asyncio.gather(
        lift(), _charging(factory, constants, charger_id, cell_id, held), return_exceptions=True
    )

    assert lifted == pytest.approx(1), lifted
    assert isinstance(charged, battery.BatteryError), charged
    assert charged.key == "battery-not-here", charged.key
    async with factory() as db:
        thing = await db.get(Item, cell_id)
        me = await db.get(Body, lifter_id)
        assert thing is not None and me is not None
        assert thing.container_id == (await world.body_container(db, me)).id
        assert not thing.charge, thing.charge
    assert await _pool_left(factory, constants, node_id) == pytest.approx(POOL_ENERGY)
    assert await _purse_of(factory, identity_id) == purse


async def test_a_cell_burnt_while_the_charge_waited_is_refused_in_words(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
) -> None:
    """A charge into a cell the fire took while it reached is the world's answer.

    The fire takes what lies in the yard (`plates._burn`) -- the cell with it
    -- and holds the rows. The charger, having seen the cell lying there, walks
    into its row. The fire commits. The lock then finds no row, and what came
    back was a failed refresh -- the server's failure -- rather than the
    world's ordinary answer (D-011): the cell is gone, and nothing is drawn or
    billed for it.
    """
    node, charger, _, cell = await _cell_on_the_floor(session, constants)
    node_id, charger_id, cell_id = node.id, charger.id, cell.id
    identity_id = charger.identity_id
    await session.commit()
    purse = await _purse_of(factory, identity_id)

    held = asyncio.Event()

    async def burn() -> float:
        async with factory() as db, db.begin():
            spot = await db.get(Node, node_id)
            assert spot is not None
            burnt = await plates._burn(db, [spot])
            held.set()
            await _until_blocked_by(factory, db)
            return burnt

    burnt, charged = await asyncio.gather(
        burn(), _charging(factory, constants, charger_id, cell_id, held), return_exceptions=True
    )

    assert not isinstance(burnt, BaseException), burnt
    assert isinstance(charged, battery.BatteryError), charged
    assert charged.key == "thing-gone", charged.key
    async with factory() as db:
        assert await db.get(Item, cell_id) is None
    assert await _pool_left(factory, constants, node_id) == pytest.approx(POOL_ENERGY)
    assert await _purse_of(factory, identity_id) == purse
