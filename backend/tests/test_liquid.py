# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Liquids live in vessels (D-230).

Checked is what the rule exists for:

* the **vault** says what is a liquid and what is a vessel -- no name in code;
* a chest refuses a liquid and a vessel refuses anything else;
* a batch **draws** water out of the canister in the hands without the recipe
  knowing, and its liquid output is **poured** into a vessel -- or spilled;
* pouring is the one way a liquid moves, and the target is locked: two hoses
  into one tank do not overfill it;
* a full canister weighs its fill.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.engine import craft, gear, jobs, liquid, storage, world
from src.models.estate import Building
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node
from src.units import amount_float

WATER = "Вода"
SPIRIT = "Спирт"
SUGAR = "Сахар"
CANISTER = "Канистра"
TANK = "Топливный бак"
CHEST = "Сундук"
PIPE = "Труба"
COMPOST = "Компост"
WASTE = "Органические отходы"
VAT = "Бродильный чан"
BENCH = "Верстак"


async def _home(session: AsyncSession, *machines: str):
    """Own plot with a building and the named machines in it."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.home.{stamp}", "Дом", area_m2=200)
    node.owner_city_id = uuid.uuid4()
    session.add(Building(node_id=node.id, area_m2=200))
    await session.flush()
    identity = await world.create_identity(session, f"Хозяин-{stamp}")
    body = await world.print_body(session, identity, node)
    await world.grant_node(session, node, identity)
    yard = await world.node_container(session, node)
    for machine in machines:
        await world.grant_item(session, yard, machine, quality=60, origin="тест")
    return node, identity, body


async def _in_hands(session: AsyncSession, body, type_key: str, amount: float = 1):
    pocket = await world.body_container(session, body)
    return await world.grant_item(
        session, pocket, type_key, amount=amount, quality=55, origin="тест"
    )


async def _filled(session: AsyncSession, vessel: Item, liquid_name: str, amount: float) -> Item:
    inside = await storage.inside(session, vessel)
    return await world.grant_item(
        session, inside, liquid_name, amount=amount, quality=55, origin="тест"
    )


async def _inside(session: AsyncSession, vessel: Item, liquid_name: str) -> float:
    return sum(
        amount_float(thing.amount)
        for thing in await storage.content(session, vessel)
        if thing.type_key == liquid_name
    )


def test_vault_marks_liquids_and_vessels(catalog: Catalog) -> None:
    """A list in the data and a field on the recipe, not names in code (D-090)."""
    assert liquid.is_liquid(catalog, WATER)
    assert liquid.is_liquid(catalog, "Ракетное топливо")
    assert not liquid.is_liquid(catalog, PIPE)
    assert liquid.is_vessel(catalog, CANISTER) and liquid.is_vessel(catalog, TANK)
    assert not liquid.is_vessel(catalog, CHEST)
    #: A vessel takes liquids only; a chest takes everything but.
    assert liquid.admits(catalog, CANISTER, WATER)
    assert not liquid.admits(catalog, CANISTER, PIPE)
    assert liquid.admits(catalog, CHEST, PIPE)
    assert not liquid.admits(catalog, CHEST, WATER)


async def test_chest_refuses_a_liquid_and_a_vessel_refuses_a_pipe(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, _, body = await _home(session, CHEST, TANK)
    yard = await world.node_container(session, node)
    chest = next(t for t in await world.contents(session, yard) if t.type_key == CHEST)
    tank = next(t for t in await world.contents(session, yard) if t.type_key == TANK)

    pipe = await _in_hands(session, body, PIPE, 2)
    with pytest.raises(storage.StorageError, match="только жидкость"):
        await storage.put(session, constants, catalog, body, tank, pipe)
    #: Water in the hands happens only in a test: the world never puts it there.
    water = await _in_hands(session, body, WATER, 5)
    with pytest.raises(storage.StorageError, match="в таре"):
        await storage.put(session, constants, catalog, body, chest, water)


async def test_batch_draws_water_from_the_canister(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Compost is waste and water; the water is in the canister, and the recipe
    does not know that (D-230)."""
    _, identity, body = await _home(session, BENCH)
    await world.learn(session, identity, COMPOST)
    await _in_hands(session, body, WASTE, 50)
    canister = await _in_hands(session, body, CANISTER)

    with pytest.raises(craft.NotEnough):
        await craft.start(session, constants, catalog, body, COMPOST, 1)

    await _filled(session, canister, WATER, 50)
    before = await _inside(session, canister, WATER)
    await craft.start(session, constants, catalog, body, COMPOST, 1)
    assert await _inside(session, canister, WATER) < before, "вода ушла из канистры"


async def test_liquid_output_is_poured_into_a_vessel_or_spilled(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Spirit comes out of the vat into the canister in the hands; with no room
    anywhere it is spilled -- and the journal says so."""
    async with factory() as session, session.begin():
        _, identity, body = await _home(session, VAT)
        await world.learn(session, identity, SPIRIT)
        await _in_hands(session, body, SUGAR, 50)
        water = await _in_hands(session, body, CANISTER)
        await _filled(session, water, WATER, 20)
        await _in_hands(session, body, CANISTER)
        batch = await craft.start(session, constants, catalog, body, SPIRIT, 1)
        ready, body_id = batch.ready_at, body.id

    await jobs.run_one(factory, now=ready)

    async with factory() as session, session.begin():
        pocket = await world.body_container(session, await session.get(Body, body_id))
        #: Into whichever canister had room -- the one with the water left in it
        #: comes first by id, and a vessel holds more than one liquid.
        held = [
            await _inside(session, vessel, SPIRIT)
            for vessel in await liquid.vessels_in(session, catalog, pocket)
        ]
        assert sum(held) > 0, "спирт налит в канистру"
        loose = await session.scalar(
            select(Item.id).where(Item.container_id == pocket.id, Item.type_key == SPIRIT)
        )
        assert loose is None, "в руках жидкости не лежит"

        #: Fill every canister to the brim: the next batch has nowhere to go.
        for vessel in await liquid.vessels_in(session, catalog, pocket):
            room = await liquid.free_in(session, catalog, vessel)
            unit = catalog.recipes.mass_of(WATER)
            if room > 0:
                await _filled(session, vessel, WATER, room / unit)
        body = await session.get(Body, body_id)
        await _in_hands(session, body, SUGAR, 50)
        batch = await craft.start(session, constants, catalog, body, SPIRIT, 1)
        ready = batch.ready_at

    await jobs.run_one(factory, now=ready)

    async with factory() as session:
        spilled = (
            (await session.execute(select(Event).where(Event.kind == EventKind.STORAGE_SPILLED)))
            .scalars()
            .all()
        )
        #: The batch drank some water out of the canisters and freed that much
        #: room, so part of the spirit went in -- and the rest had nowhere to go.
        assert len(spilled) == 1 and spilled[0].payload["type_key"] == SPIRIT
        assert spilled[0].payload["amount"] > 0


async def test_pour_moves_liquid_and_nothing_else(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, _, body = await _home(session, TANK)
    yard = await world.node_container(session, node)
    tank = next(t for t in await world.contents(session, yard) if t.type_key == TANK)
    canister = await _in_hands(session, body, CANISTER)
    await _filled(session, canister, WATER, 30)

    poured, how_much = await liquid.pour(session, constants, catalog, body, canister, tank)
    assert poured == WATER and how_much == pytest.approx(30)
    assert await _inside(session, tank, WATER) == pytest.approx(30)
    assert await _inside(session, canister, WATER) == 0

    #: Back into the hands -- under the carry limit, and only what fits the canister.
    _, back = await liquid.pour(session, constants, catalog, body, tank, canister, WATER, 500)
    assert back * catalog.recipes.mass_of(WATER) <= storage.capacity(catalog, CANISTER)
    with pytest.raises(liquid.LiquidError):
        await liquid.pour(session, constants, catalog, body, canister, canister)
    chest = await world.grant_item(session, yard, CHEST, quality=60, origin="тест")
    with pytest.raises(liquid.NotVessel):
        await liquid.pour(session, constants, catalog, body, canister, chest)


async def test_two_hoses_do_not_overfill_one_tank(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The free space is read, checked and filled; without the lock on the
    target both pours see the same half-empty tank (review 2026-08-23)."""
    node, _, body = await _home(session, TANK)
    yard = await world.node_container(session, node)
    tank = next(t for t in await world.contents(session, yard) if t.type_key == TANK)
    limit = storage.capacity(catalog, TANK)
    unit = catalog.recipes.mass_of(WATER)
    #: Nearly full: what is left takes one canister, not two.
    await _filled(session, tank, WATER, (limit - 15) / unit)
    hoses = []
    for _ in range(2):
        canister = await _in_hands(session, body, CANISTER)
        await _filled(session, canister, WATER, 15 / unit)
        hoses.append(canister.id)
    tank_id, body_id = tank.id, body.id
    await session.commit()

    async def pour(canister_id: uuid.UUID) -> None:
        async with factory() as db, db.begin():
            mover = await db.get(Body, body_id)
            await liquid.pour(
                db,
                constants,
                catalog,
                mover,
                await db.get(Item, canister_id),
                await db.get(Item, tank_id),
            )

    outcomes = await asyncio.gather(*(pour(h) for h in hoses), return_exceptions=True)
    refused = [o for o in outcomes if isinstance(o, liquid.NoRoom)]
    assert len(refused) == 1, outcomes
    async with factory() as db:
        tank = await db.get(Item, tank_id)
        assert await storage.stored_mass(db, catalog, tank) <= limit + 1e-6


async def test_a_full_canister_weighs_its_fill(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _home(session)
    canister = await _in_hands(session, body, CANISTER)
    empty = await gear.load_of(session, catalog, body)
    await _filled(session, canister, WATER, 40)
    full = await gear.load_of(session, catalog, body)
    assert full == pytest.approx(empty + 40 * catalog.recipes.mass_of(WATER))


async def test_the_worker_and_the_owner_do_not_overfill_one_canister(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch ending in a liquid (`settle`, by the worker) and a pour by the
    owner into the same canister: both read its free space, and without the
    row lock both see it half empty and the canister ends over the brim."""
    node, _, body = await _home(session)
    unit = catalog.recipes.mass_of(WATER)
    limit = storage.capacity(catalog, CANISTER)
    #: The target stands in the room: in the hands the carry limit would
    #: refuse the pour before the free space is ever read.
    yard = await world.node_container(session, node)
    target = await world.grant_item(session, yard, CANISTER, quality=55, origin="тест")
    await _filled(session, target, WATER, (limit - 3) / unit)
    hose = await _in_hands(session, body, CANISTER)
    await _filled(session, hose, WATER, 3 / unit)
    #: The batch's output: a loose stack the way `_finish_make` leaves it
    #: for a moment before pouring -- in the hands, only in a test.
    fresh = await _in_hands(session, body, WATER, 3 / unit)
    body_id, target_id, hose_id, fresh_id = body.id, target.id, hose.id, fresh.id
    await session.commit()
    #: Hold each side between reading the free space and writing.
    _slow(monkeypatch, liquid, "free_in")

    async def worker() -> float:
        async with factory() as db, db.begin():
            mover = await db.get(Body, body_id)
            room = await world.node_container(db, await db.get(Node, mover.node_id))
            return await liquid.settle(db, catalog, await db.get(Item, fresh_id), [room])

    async def owner() -> None:
        async with factory() as db, db.begin():
            mover = await db.get(Body, body_id)
            await liquid.pour(
                db,
                constants,
                catalog,
                mover,
                await db.get(Item, hose_id),
                await db.get(Item, target_id),
            )

    outcomes = await asyncio.gather(worker(), owner(), return_exceptions=True)
    #: One of the two got the last three kilograms; the other spilled or was refused.
    spilled = outcomes[0] if isinstance(outcomes[0], float) else 0.0
    refused = isinstance(outcomes[1], liquid.NoRoom)
    assert (spilled > 0) != refused, outcomes
    async with factory() as db:
        assert await storage.stored_mass(db, catalog, await db.get(Item, target_id)) <= limit + 1e-6


def _slow(monkeypatch: pytest.MonkeyPatch, module: object, name: str, delay: float = 0.2) -> None:
    """Hold the transaction between its check and its write (as in `test_races`)."""
    original = getattr(module, name)

    async def held(*args, **kwargs):
        result = await original(*args, **kwargs)
        await asyncio.sleep(delay)
        return result

    monkeypatch.setattr(module, name, held)
