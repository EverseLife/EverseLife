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

A cell charged from the pool (`battery.charge_battery`) is such a stack too --
a battery is an input to a feed circuit and an exoskeleton -- so the charge
takes the cell before the pool, and asks where it lies of its row once that
lock is had. A cell lifted off the floor while the charge waited on it is in
the lifter's hands, and one deleted meanwhile is nowhere; neither is charged,
nor billed to the charger. And an automat eating the cell holds it from its
advance and takes the pool only at the end of its pass: a charge holding the
pool while it waited for that cell was a deadlock.

The lift and the deletion that go first are `gone_kit`'s.

The handshake is `conftest._until_blocked_by`: the side holding the contended rows
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

from automat_kit import IRON, NAILS, _factory_floor, _learn, _lube_in, _pool_left
from conftest import _hold_the_first, _until_blocked_by
from gone_kit import _burning
from src.constants import Catalog, Constants, current
from src.constants import registry as R
from src.engine import (
    automat,
    battery,
    craft,
    death,
    energy,
    ledger,
    liquid,
    station,
    storage,
    world,
)
from src.engine.craft import procedure
from src.models.automat import Automat as AutomatRow
from src.models.craft import CraftBatch
from src.models.energy import EnergyPool
from src.models.identity import Body, Identity
from src.models.inventory import Container, Item
from src.models.job import Job, JobKind
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node
from src.units import PERCENT, amount_float, money

FURNACE = "blast_furnace"
SILICON = "silicon"
SAND = "quartz_sand"
COKE = "petroleum_coke"
PLANT = "coal_plant"
OIL = "crude_oil"
#: A thing the assembler makes with a battery inside (D-253).
CIRCUIT = "feed_circuit"
#: Energy the pool holds for a charge: more than a cell takes, so the cell's
#: room decides the pour and the bill.
POOL_ENERGY = 1000


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


async def _cell_standing(session: AsyncSession, constants: Constants):
    """A city yard on the grid, an empty cell standing in it -- the only kind
    a counter charges (D-352) -- a funded charger and a lifter beside it.

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
        installed=True,
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


async def _carrying_off(
    factory: async_sessionmaker[AsyncSession],
    catalog: Catalog,
    lifter_id: uuid.UUID,
    cell_id: uuid.UUID,
    held: asyncio.Event,
) -> float:
    """Take the standing cell down and pick it up, in one transaction that
    holds the cell's row until the charge provably waits on it. Two doors,
    because a standing thing comes into the hands through two (D-308)."""
    async with factory() as db, db.begin():
        me = await db.get(Body, lifter_id)
        thing = await db.get(Item, cell_id)
        assert me is not None and thing is not None
        await station.take(db, catalog, me, thing)
        taken = await storage.pick(db, current(), catalog, me, thing)
        held.set()
        await _until_blocked_by(factory, db)
        return taken


async def _purse_of(factory: async_sessionmaker[AsyncSession], identity_id: uuid.UUID) -> int:
    async with factory() as db:
        account = await ledger.account_for(db, AccountKind.IDENTITY, identity_id)
        return await ledger.balance(db, account.id)


async def test_a_cell_carried_off_is_not_charged_in_the_lifters_hands(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A charge asks where the cell is of its row after the lock, not before.

    The lifter takes the standing cell down and picks it up, and keeps the
    transaction open, holding its row. The charger sees the cell still
    standing in the yard -- the one place a counter charges (D-352) -- and
    walks into the row. The carrying-off commits. Judged by the sight from
    before the wait, the charge went on into a cell now in the lifter's hands:
    the city's energy poured into another body's pocket, and the charger billed
    for it. Judged after the wait, the cell is no longer here.
    """
    node, charger, lifter, cell = await _cell_standing(session, constants)
    node_id, charger_id, lifter_id, cell_id = node.id, charger.id, lifter.id, cell.id
    identity_id = charger.identity_id
    await session.commit()
    purse = await _purse_of(factory, identity_id)

    held = asyncio.Event()

    lifted, charged = await asyncio.gather(
        _carrying_off(factory, catalog, lifter_id, cell_id, held),
        _charging(factory, constants, charger_id, cell_id, held),
        return_exceptions=True,
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


async def test_a_cell_deleted_while_the_charge_waited_is_refused_by_key(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
) -> None:
    """A charge into a cell taken out of the world while it reached is refused by key.

    The yard is burnt (`plates._burn`) -- the cell with it -- and the rows are
    held. The fire of Pyroxis never reaches a city's counter; it stands in for
    the ways a cell does leave the world there, a falling house (D-244) or an
    automat eating it. The charger, having seen the cell standing there, walks
    into its row. The deletion commits. The lock then finds no row, and what
    came back was a failed refresh -- the server's failure -- rather than a
    refusal by key (D-251): the cell is gone, and nothing is drawn or billed
    for it.
    """
    node, charger, _, cell = await _cell_standing(session, constants)
    node_id, charger_id, cell_id = node.id, charger.id, cell.id
    identity_id = charger.identity_id
    await session.commit()
    purse = await _purse_of(factory, identity_id)

    held = asyncio.Event()

    burnt, charged = await asyncio.gather(
        _burning(factory, node_id, held),
        _charging(factory, constants, charger_id, cell_id, held),
        return_exceptions=True,
    )

    assert not isinstance(burnt, BaseException), burnt
    assert isinstance(charged, battery.BatteryError), charged
    assert charged.key == "thing-gone", charged.key
    async with factory() as db:
        assert await db.get(Item, cell_id) is None
    assert await _pool_left(factory, constants, node_id) == pytest.approx(POOL_ENERGY)
    assert await _purse_of(factory, identity_id) == purse


async def test_a_cell_the_tick_may_eat_is_refused_before_any_lock(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """An assembler makes a feed circuit of the cell lying by it, and its owner
    tries to charge that very cell at the counter.

    That race once deadlocked: the charge held the pool waiting for the cell
    while the tick held the cell waiting for the pool (`automat.bill`). With
    D-352 it cannot even begin. What an automat eats lies (D-278), and a lying
    cell is refused before a single row is taken -- so the charge never
    queues on a cell the tick may hold, and the tick eats it undisturbed.
    """
    node, yard, identity, body, assembler = await _factory_floor(session, constants)
    proc = procedure(catalog, CIRCUIT)
    #: What the race needs of the vault, asserted rather than assumed: one
    #: whole cell goes into one circuit, so the piece eats the row outright.
    assert proc.per_unit.get(battery.BATTERY) == pytest.approx(1), proc.per_unit
    cell = await world.grant_item(
        session, yard, battery.BATTERY, quality=60, origin="test", installed=False
    )
    #: The other inputs for more than one piece: the cell alone caps the work at one.
    for name, per in proc.per_unit.items():
        if name != battery.BATTERY:
            await world.grant_item(session, yard, name, amount=3 * per, quality=60, origin="test")
    await _lube_in(session, yard, 1000)
    await _learn(session, identity, CIRCUIT)
    row = await automat.program(session, constants, catalog, body, assembler, CIRCUIT)
    pool = await energy.pool_of(session, constants, node)
    assert pool is not None
    pool.counted_at = row.counted_at
    #: Twice the hours one piece takes (`run.advance`): time for one, inputs for one.
    unit_hours = proc.step_hours / (constants[R.AUTO_SPEED_SHARE] / PERCENT)
    moment = row.counted_at + timedelta(hours=2 * unit_hours)
    body_id, cell_id, yard_id = body.id, cell.id, yard.id
    await session.commit()

    async with factory() as db, db.begin():
        me = await db.get(Body, body_id)
        item = await db.get(Item, cell_id)
        assert me is not None and item is not None
        with pytest.raises(battery.BatteryError) as refused:
            await battery.charge_battery(db, constants, me, item)
    assert refused.value.key == "battery-charge-lying", refused.value.key

    async with factory() as db, db.begin():
        made = await automat.tick_automats(db, constants, now=moment)
    assert made == pytest.approx(1), made
    async with factory() as db:
        assert await db.get(Item, cell_id) is None
        circuits = [
            thing
            for thing in await world.contents(db, await db.get_one(Container, yard_id))
            if thing.type_key == CIRCUIT
        ]
        assert sum(amount_float(thing.amount) for thing in circuits) == pytest.approx(1)
