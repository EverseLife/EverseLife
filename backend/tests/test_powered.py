# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A machine on electricity (D-269).

Energy is a condition of the machine's work, not an input of the recipe: the
vault marks the machine `powered`, and a manual batch at it draws
`craft.powered_energy_per_hour` for its hours, up front like the materials.
Checked:

* the three recipes that listed energy no longer do, and the flag is data;
* the forecast names the energy and its price, and drinks nothing;
* the start draws from the city pool at the tariff and bills the master;
* a pool too low refuses before a material is touched;
* outside a city the cells standing beside the machine feed it, unbilled;
* a machine driven by the hands draws nothing and needs no grid;
* a try without a recipe runs the machine too;
* two masters at two machines cannot both drink the last hour of one pool.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import battery, craft, energy, ledger, world
from src.engine.craft import power
from src.models.craft import CraftBatch
from src.models.identity import Body
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer
from src.units import ENERGY_PER_TARIFF_UNIT, MINUTES_PER_HOUR, amount_float, money

FURNACE = "blast_furnace"
SILICON = "silicon"
SAND = "quartz_sand"
COKE = "petroleum_coke"
BENCH = "workbench"
HANDLE = "handle"
WOOD = "wood"
CELL = "battery"


async def _shop(session: AsyncSession, *, city: bool, machine: str = FURNACE, knows: str = SILICON):
    """A master at a machine: in a city yard with a pool, or on a bare planet node."""
    stamp = uuid.uuid4().hex[:8]
    if city:
        capital = await world.create_node(
            session, f"terra.power.{stamp}", "Столица", area_m2=1, layer=Layer.PLANET
        )
        node = await world.create_node(
            session,
            f"terra.power.{stamp}.yard",
            "Двор",
            area_m2=200,
            layer=Layer.CITY,
            parent=capital,
        )
    else:
        node = await world.create_node(
            session, f"terra.wild.{stamp}", "Пустошь", area_m2=200, layer=Layer.PLANET
        )
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, machine, quality=60, origin="тест")
    identity = await world.create_identity(session, f"Мастер-{stamp}")
    body = await world.print_body(session, identity, node)
    await world.learn(session, identity, knows)
    pocket = await world.body_container(session, body)
    for name, qty in ((SAND, 40), (COKE, 20), (WOOD, 30)):
        await world.grant_item(session, pocket, name, amount=qty, quality=60, origin="тест")
    return node, identity, body


async def _fund(session: AsyncSession, identity, amount: float) -> None:
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=genesis.id,
        credit=account.id,
        amount=money(amount),
        memo={},
    )


async def _pool(session: AsyncSession, constants: Constants, node, stored: float):
    pool = await energy.pool_of(session, constants, node)
    assert pool is not None
    pool.stored = Decimal(str(stored))
    pool.counted_at = datetime.now(UTC)
    await session.flush()
    return pool


async def _held(session: AsyncSession, body: Body) -> dict[str, float]:
    pocket = await world.body_container(session, body)
    rows = (await session.execute(select(Item).where(Item.container_id == pocket.id))).scalars()
    return {item.type_key: amount_float(item.amount) for item in rows}


def test_energy_is_a_condition_of_the_machine_not_an_input(catalog: Catalog) -> None:
    """The three recipes that listed energy no longer do, and which machines run
    on it is the vault's word, not a guess by name (D-269)."""
    book = catalog.recipes
    for name in (SILICON, "oxygen", "oxidizer"):
        assert "energy" not in book.recipe(name).amounts, name
    assert book.powered(FURNACE) and book.powered("electrolyzer")
    assert not book.powered(BENCH) and not book.powered("forge")


async def test_forecast_names_the_energy_and_its_price(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The exact number before the batch (D-092), and nothing drunk for it."""
    node, _, body = await _shop(session, city=True)
    await _pool(session, constants, node, 1000)
    plan = await craft.plan(session, constants, catalog, body, SILICON, 2)
    rate = constants[R.CRAFT_POWERED_ENERGY_PER_HOUR]
    assert plan.energy == pytest.approx(plan.minutes / MINUTES_PER_HOUR * rate)
    assert plan.energy > 0
    assert plan.price == money(
        plan.energy / ENERGY_PER_TARIFF_UNIT * constants[R.ENERGY_TARIFF_DEFAULT]
    )
    pool = await energy.pool_of(session, constants, node)
    assert pool is not None and float(pool.stored) == pytest.approx(1000), "прогноз читает, не пьёт"


async def test_the_start_drinks_from_the_pool_and_bills_the_master(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Whoever burns pays (D-135): the pool falls by the batch's hours, the
    treasury gains the tariff, the master's account loses it."""
    node, identity, body = await _shop(session, city=True)
    await _fund(session, identity, 100)
    await _pool(session, constants, node, 1000)
    plan = await craft.plan(session, constants, catalog, body, SILICON, 2)

    batch = await craft.start(session, constants, catalog, body, SILICON, 2)

    assert batch.output == SILICON
    pool = await energy.pool_of(session, constants, node)
    assert pool is not None and float(pool.stored) == pytest.approx(1000 - plan.energy)
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    treasury = await ledger.account_for(session, AccountKind.CITY_TREASURY, pool.node_id)
    assert await ledger.balance(session, treasury.id) == plan.price
    assert await ledger.balance(session, account.id) == money(100) - plan.price


async def test_a_low_pool_refuses_before_a_material_is_touched(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Written off up front like the materials -- and refused before them."""
    node, identity, body = await _shop(session, city=True)
    await _fund(session, identity, 100)
    await _pool(session, constants, node, 0)
    before = await _held(session, body)

    with pytest.raises(power.Unpowered):
        await craft.start(session, constants, catalog, body, SILICON, 2)

    assert await _held(session, body) == before
    begun = (
        (await session.execute(select(CraftBatch).where(CraftBatch.body_id == body.id)))
        .scalars()
        .all()
    )
    assert begun == []


async def test_outside_a_city_the_cells_beside_the_machine_feed_it(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No grid, no tariff and no bill: the energy was bought at charging
    (D-071), and it is spent where the cell stands."""
    node, identity, body = await _shop(session, city=False)
    with pytest.raises(power.Unpowered):
        await craft.start(session, constants, catalog, body, SILICON, 2)

    yard = await world.node_container(session, node)
    cell = await world.grant_item(session, yard, CELL, quality=60, origin="тест")
    cell.charge = Decimal("1")
    cell.charged_at = datetime.now(UTC)
    await session.flush()
    with pytest.raises(power.Unpowered):
        await craft.start(session, constants, catalog, body, SILICON, 2)

    cell.charge = Decimal("50")
    await session.flush()
    plan = await craft.plan(session, constants, catalog, body, SILICON, 2)
    assert plan.energy is not None and plan.energy > 0
    #: No grid, no price at all: the key is absent rather than nought, and
    #: the window tells the cells apart from the tariff by that (D-225).
    assert plan.price is None, "вне города за ток не платят"
    await craft.start(session, constants, catalog, body, SILICON, 2)
    await session.refresh(cell)
    assert float(cell.charge) == pytest.approx(50 - plan.energy, abs=0.01)
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    assert await ledger.balance(session, account.id) == 0


async def test_a_machine_driven_by_the_hands_needs_no_grid(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The hand bench draws nothing, and the plan carries no key for it (D-225)."""
    _, _, body = await _shop(session, city=False, machine=BENCH, knows=HANDLE)
    plan = await craft.plan(session, constants, catalog, body, HANDLE, 1)
    assert plan.energy is None and plan.price is None
    batch = await craft.start(session, constants, catalog, body, HANDLE, 1)
    assert batch.output == HANDLE


async def test_a_try_without_a_recipe_runs_the_machine(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A try runs the machine for the base time per unit tried, matched or
    not; the prototype that follows pays for its own hours like any batch."""
    node, identity, body = await _shop(session, city=True, knows=HANDLE)
    await _fund(session, identity, 100)
    await _pool(session, constants, node, 1000)
    rate = constants[R.CRAFT_POWERED_ENERGY_PER_HOUR]
    tried = 2 * constants[R.CRAFT_TIME_PER_UNIT] / MINUTES_PER_HOUR * rate

    #: The vault's own norm, not a number: the build derives the amounts from
    #: labour, and a try is matched against exactly that (D-209).
    norm = catalog.recipes.recipe(SILICON).amounts
    found = await craft.invent(
        session,
        constants,
        catalog,
        body,
        {SAND: norm[SAND], COKE: norm[COKE]},
        2,
        station=FURNACE,
    )
    assert found.learned == (SILICON,) and found.batch is not None
    plan = await craft.plan(session, constants, catalog, body, SILICON, 2)
    pool = await energy.pool_of(session, constants, node)
    assert pool is not None
    assert float(pool.stored) == pytest.approx(1000 - tried - plan.energy)

    missed = await craft.invent(
        session, constants, catalog, body, {SAND: 1, COKE: 1}, 2, station=FURNACE
    )
    assert missed.learned == ()
    assert float(pool.stored) == pytest.approx(1000 - tried - plan.energy - tried)


async def test_two_masters_do_not_both_drink_the_last_hour(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pool is a remainder like money: two starts that both read enough
    must not both spend it. The pool row serialises them."""
    _slow(monkeypatch, energy, "produce")
    node, first, one = await _shop(session, city=True)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, FURNACE, quality=60, origin="тест")
    stamp = uuid.uuid4().hex[:8]
    second = await world.create_identity(session, f"Второй-{stamp}")
    two = await world.print_body(session, second, node)
    await world.learn(session, second, SILICON)
    pocket = await world.body_container(session, two)
    for name, qty in ((SAND, 40), (COKE, 20)):
        await world.grant_item(session, pocket, name, amount=qty, quality=60, origin="тест")
    await _fund(session, first, 100)
    await _fund(session, second, 100)
    plan = await craft.plan(session, constants, catalog, one, SILICON, 2)
    enough_for_one = plan.energy * 1.5
    await _pool(session, constants, node, enough_for_one)
    ids = (one.id, two.id)
    node_id = node.id
    await session.commit()

    async def go(body_id: uuid.UUID) -> bool:
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            assert me is not None
            try:
                await craft.start(db, constants, catalog, me, SILICON, 2)
            except power.Unpowered:
                return False
            return True

    done = await asyncio.gather(*(go(body_id) for body_id in ids))
    assert sorted(done) == [False, True], "последний час выпит один раз"
    async with factory() as db:
        again = await db.get(type(node), node_id)
        assert again is not None
        pool = await energy.pool_of(db, constants, again, create=False)
        assert pool is not None
        assert float(pool.stored) == pytest.approx(enough_for_one - plan.energy, abs=0.01)


async def test_two_masters_do_not_both_drink_the_last_cell(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside a city the cell is the remainder: two starts that both read a
    full cell must not both drain it. The cell rows are locked in id order."""
    _slow(monkeypatch, battery, "batteries_in")
    node, first, one = await _shop(session, city=False)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, FURNACE, quality=60, origin="тест")
    stamp = uuid.uuid4().hex[:8]
    second = await world.create_identity(session, f"Второй-{stamp}")
    two = await world.print_body(session, second, node)
    await world.learn(session, second, SILICON)
    pocket = await world.body_container(session, two)
    for name, qty in ((SAND, 40), (COKE, 20)):
        await world.grant_item(session, pocket, name, amount=qty, quality=60, origin="тест")
    plan = await craft.plan(session, constants, catalog, one, SILICON, 2)
    cell = await world.grant_item(session, yard, CELL, quality=60, origin="тест")
    enough_for_one = plan.energy * 1.5
    cell.charge = Decimal(str(enough_for_one))
    cell.charged_at = datetime.now(UTC)
    ids = (one.id, two.id)
    cell_id = cell.id
    await session.commit()

    async def go(body_id: uuid.UUID) -> bool:
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            assert me is not None
            try:
                await craft.start(db, constants, catalog, me, SILICON, 2)
            except power.Unpowered:
                return False
            return True

    done = await asyncio.gather(*(go(body_id) for body_id in ids))
    assert sorted(done) == [False, True], "последняя ячейка выпита один раз"
    async with factory() as db:
        again = await db.get(Item, cell_id)
        assert again is not None
        assert float(again.charge) == pytest.approx(enough_for_one - plan.energy, abs=0.01)
