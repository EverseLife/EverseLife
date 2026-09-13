# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The automats' tick: every machine of the world in one pass (D-253).

Cut out of `test_automat.py`, which keeps one machine at work. Here is what
only the world's pass has: the energy asked for before each machine works and
drawn once all of them have (`automat/bill.py`), so machines on one pool, one
purse or one hull's cells share them rather than each spending the whole; a
node's meter read once a pass and answering for that node alone (D-149); and
one machine failing inside its own savepoint while the floor works on -- in
the step itself and through the worker's job runner. The races of the same
pass against a player are in `test_races_automat.py`.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import IRON, NAILS, _factory_floor, _learn, _lube_in
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import automat, energy, jobs, ledger, utility, world
from src.engine.automat import run as automat_run
from src.engine.tick import WORLD_STEPS
from src.models.automat import Automat as AutomatRow
from src.models.inventory import Item
from src.models.job import Job, JobKind, JobState
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Node
from src.units import amount_float, money


@pytest.mark.parametrize("short", ["pool", "purse"])
async def test_machines_on_one_pool_and_one_purse_share_them_in_the_tick(
    short: str, session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The tick draws the energy once every machine has worked, but asks for
    it before each one works -- and what an earlier machine was promised is
    gone for the next. A pool, or a purse, holding one machine's hours powers
    those hours once, as drawing them one machine after another did: not twice
    over, the second on credit."""
    hours = 10
    enough = constants[R.AUTO_ENERGY_PER_HOUR] * hours
    node, yard, identity, body, assembler = await _factory_floor(
        session,
        constants,
        stored_energy=enough if short == "pool" else 10_000,
        funded=short == "pool",
    )
    pool = await energy.pool_of(session, constants, node)
    assert pool is not None
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    if short == "purse":
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=account.id,
            amount=energy.price_at(constants, pool, enough),
            memo={},
        )
    smelter = await world.grant_item(session, yard, "auto_furnace", quality=70, origin="test")
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    await world.grant_item(session, yard, "iron_ore", amount=4000, quality=60, origin="test")
    await world.grant_item(session, yard, "coal", amount=1000, quality=60, origin="test")
    lube = await _lube_in(session, yard, 100)
    await _learn(session, identity, NAILS)
    first = await automat.program(session, constants, catalog, body, assembler, NAILS)
    second = await automat.program(session, constants, catalog, body, smelter, IRON)
    second.counted_at = first.counted_at
    await session.flush()

    await automat.tick_automats(session, constants, now=first.counted_at + timedelta(hours=hours))

    await session.refresh(lube)
    await session.refresh(pool)
    burnt = 100 - amount_float(lube.amount)
    assert burnt == pytest.approx(hours * constants[R.AUTO_LUBE_PER_HOUR], rel=0.01), (
        "one machine's hours were powered, not two"
    )
    if short == "pool":
        assert float(pool.stored) == pytest.approx(0, abs=0.01)
    else:
        assert float(pool.stored) == pytest.approx(10_000 - enough, abs=0.01)
        assert await ledger.balance(session, account.id) == 0


async def test_machines_on_one_hull_share_its_cells_in_the_tick(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Where no grid reaches, the supply is the cells standing beside the
    machines (D-071), and the tick shares them the way it shares a pool: what
    the first machine was promised is gone for the second, so a cell holding a
    few hours powers those hours once, and is drained to nought and no lower."""
    stamp = uuid.uuid4().hex[:8]
    wild = await world.create_node(session, f"terra.wild.{stamp}", "Wilds", area_m2=200)
    identity = await world.create_identity(session, f"Hermit-{stamp}")
    body = await world.print_body(session, identity, wild)
    yard = await world.node_container(session, wild)
    assembler = await world.grant_item(session, yard, "auto_station", quality=70, origin="test")
    smelter = await world.grant_item(session, yard, "auto_furnace", quality=70, origin="test")
    cell = await world.grant_item(session, yard, "battery", quality=60, origin="test")
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    await world.grant_item(session, yard, "iron_ore", amount=4000, quality=60, origin="test")
    await world.grant_item(session, yard, "coal", amount=1000, quality=60, origin="test")
    lube = await _lube_in(session, yard, 100)
    await _learn(session, identity, NAILS)
    first = await automat.program(session, constants, catalog, body, assembler, NAILS)
    second = await automat.program(session, constants, catalog, body, smelter, IRON)
    second.counted_at = first.counted_at
    hours, powered = 10, 3
    moment = first.counted_at + timedelta(hours=hours)
    rate = constants[R.AUTO_ENERGY_PER_HOUR]
    #: Charged at the tick's own moment, so no self-discharge blurs the hours.
    cell.charge = Decimal(str(rate * powered))
    cell.charged_at = moment
    await session.flush()

    await automat.tick_automats(session, constants, now=moment)

    await session.refresh(lube)
    await session.refresh(cell)
    burnt = 100 - amount_float(lube.amount)
    assert burnt == pytest.approx(powered * constants[R.AUTO_LUBE_PER_HOUR], rel=0.01), (
        "the cell's hours were worked once, not once per machine"
    )
    assert float(cell.charge) == pytest.approx(0, abs=0.01)


@pytest.mark.parametrize("broken_first", [True, False])
async def test_a_broken_automat_is_passed_over_and_the_floor_works_on(
    broken_first: bool,
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One machine whose advance fails -- a programme the vault has since
    broken -- must not stop the world's factories. It works in a savepoint of
    its own: what it paid out, drank and was billed goes back with it, and the
    machine beside it works and pays as if it stood alone. Both orders, set by
    a wire: the bill dropped is the first on the tab or the last."""
    node, yard, identity, body, assembler = await _factory_floor(session, constants)
    smelter = await world.grant_item(session, yard, "auto_furnace", quality=70, origin="test")
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    ore = await world.grant_item(session, yard, "iron_ore", amount=4000, quality=60, origin="test")
    await world.grant_item(session, yard, "coal", amount=1000, quality=60, origin="test")
    lube = await _lube_in(session, yard, 100)
    await _learn(session, identity, NAILS)
    sound = await automat.program(session, constants, catalog, body, assembler, NAILS)
    broken = await automat.program(session, constants, catalog, body, smelter, IRON)
    if broken_first:
        await automat.link(session, body, smelter, assembler)
    else:
        await automat.link(session, body, assembler, smelter)
    started = broken.counted_at
    hours = 10
    moment = sound.counted_at + timedelta(hours=hours)
    pool = await energy.pool_of(session, constants, node)
    assert pool is not None
    stored = float(pool.stored)
    broken_id = broken.id
    await session.flush()

    real = automat_run.advance

    async def failing(*args, **kwargs):
        #: The whole advance goes through first -- the ingots paid out, the ore
        #: and the lubricant taken, the bill written -- so the savepoint has
        #: something to take back.
        made = await real(*args, **kwargs)
        if args[2].id == broken_id:
            raise RuntimeError("a programme the vault has since broken")
        return made

    monkeypatch.setattr(automat_run, "advance", failing)

    made = await automat.tick_automats(session, constants, now=moment)

    assert made > 0, "the sound machine worked"
    await session.refresh(sound)
    await session.refresh(broken)
    await session.refresh(ore)
    await session.refresh(lube)
    await session.refresh(pool)
    assert sound.counted_at == moment
    assert broken.counted_at == started, "the broken machine's hours wait for the next tick"
    assert amount_float(ore.amount) == pytest.approx(4000), "its smelting went back"
    assert 100 - amount_float(lube.amount) == pytest.approx(
        hours * constants[R.AUTO_LUBE_PER_HOUR], rel=0.01
    ), "only the sound machine drank"
    assert stored - float(pool.stored) == pytest.approx(
        hours * constants[R.AUTO_ENERGY_PER_HOUR], abs=0.01
    ), "only the sound machine's bill was drawn"


async def test_a_broken_automat_does_not_fail_the_tick_step_job(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same broken machine through the worker's own door: the step runs as a
    job (`tick.tick_step` under `jobs.run_one`), and the runner reads the job's
    row after the step. Passing a machine over must leave that row readable -- a
    step that expired the whole session under the runner failed the job, and
    with it every factory of the world, on every tick the machine stayed broken."""
    assert "automats" in WORLD_STEPS
    _, yard, identity, body, assembler = await _factory_floor(session, constants)
    smelter = await world.grant_item(session, yard, "auto_furnace", quality=70, origin="test")
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    await world.grant_item(session, yard, "iron_ore", amount=4000, quality=60, origin="test")
    await world.grant_item(session, yard, "coal", amount=1000, quality=60, origin="test")
    await _lube_in(session, yard, 100)
    await _learn(session, identity, NAILS)
    sound = await automat.program(session, constants, catalog, body, assembler, NAILS)
    broken = await automat.program(session, constants, catalog, body, smelter, IRON)
    started = broken.counted_at
    moment = sound.counted_at + timedelta(hours=10)
    step = await jobs.enqueue(
        session,
        JobKind.TICK_STEP,
        moment,
        payload={"step": "automats", "tick": "world", "at": moment.isoformat()},
        dedup_key=f"test.automats:{moment.isoformat()}",
    )
    assert step is not None
    ids = (sound.id, broken.id, step.id)
    await session.commit()
    sound_id, broken_id, step_id = ids

    real = automat_run.advance

    async def failing(*args, **kwargs):
        made = await real(*args, **kwargs)
        if args[2].id == broken_id:
            raise RuntimeError("a programme the vault has since broken")
        return made

    monkeypatch.setattr(automat_run, "advance", failing)

    while await jobs.run_one(factory, now=moment) is not None:
        pass

    async with factory() as db:
        ran = await db.get(Job, step_id)
        assert ran is not None
        assert ran.state is JobState.DONE, ran.last_error
        worked = await db.get(AutomatRow, sound_id)
        waited = await db.get(AutomatRow, broken_id)
        assert worked is not None and worked.counted_at == moment
        assert waited is not None and waited.counted_at == started


async def test_a_cut_off_node_stands_every_machine_in_it_and_none_of_its_owners_elsewhere(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tick reads a node's meter once a pass (D-149), and the answer is the
    node's: both machines on the floor in debt stand, and the same owner's
    factory on a paid floor works in the same pass. An answer kept by the owner,
    or one for the whole pass, would stand the paid floor or run the one in
    debt, whichever machine the pass happened to ask first. And a meter read per
    machine is a query a machine while the tick holds every factory's stacks."""
    node, yard, identity, body, assembler = await _factory_floor(session, constants)
    node.owner_identity_id = identity.id
    smelter = await world.grant_item(session, yard, "auto_furnace", quality=70, origin="test")
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    await world.grant_item(session, yard, "iron_ore", amount=4000, quality=60, origin="test")
    await world.grant_item(session, yard, "coal", amount=1000, quality=60, origin="test")
    lube = await _lube_in(session, yard, 100)
    await _learn(session, identity, NAILS)
    first = await automat.program(session, constants, catalog, body, assembler, NAILS)
    second = await automat.program(session, constants, catalog, body, smelter, IRON)

    elsewhere, other_yard, _, _, other_machine = await _factory_floor(session, constants)
    elsewhere.owner_identity_id = identity.id
    await world.grant_item(session, other_yard, IRON, amount=1000, quality=60, origin="test")
    await _lube_in(session, other_yard, 100)
    body.node_id = elsewhere.id
    await session.flush()
    third = await automat.program(session, constants, catalog, body, other_machine, NAILS)

    meter = await utility.meter_of(session, node)
    paid = await utility.meter_of(session, elsewhere)
    assert meter is not None and paid is not None and not paid.cut_off
    meter.cut_off, meter.debt = True, money(1)
    for row in (second, third):
        row.counted_at = first.counted_at
    moment = first.counted_at + timedelta(hours=8)
    await session.flush()

    real = utility.cut_off
    asked: list[uuid.UUID] = []

    async def counted(db: AsyncSession, where: Node) -> bool:
        asked.append(where.id)
        return await real(db, where)

    monkeypatch.setattr(utility, "cut_off", counted)

    made = await automat.tick_automats(session, constants, now=moment)

    assert sorted(asked) == sorted([node.id, elsewhere.id]), "one reading a node, not a machine"
    assert made > 0, "the owner's paid floor worked"
    await session.refresh(lube)
    assert amount_float(lube.amount) == pytest.approx(100), "neither machine in debt ran"
    nails = select(Item).where(Item.container_id == other_yard.id, Item.type_key == NAILS)
    assert (await session.execute(nails)).scalars().all()
    for row in (first, second, third):
        await session.refresh(row)
        assert row.counted_at == moment
