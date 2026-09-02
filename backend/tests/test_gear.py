# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Carried load: mass, limit and gear slots (D-146).

The carry limit was written as a constant from the very start, but items had
no mass -- and the player carried a thousand ore in the pocket. Checked is
what mass was introduced for:

* an item has weight, and it comes from vault data, not code;
* no more than the limit is taken in hand -- neither from the market nor from the hopper;
* a backpack and an exoskeleton raise the limit, clothes and armour do not;
* one slot per thing: you cannot wear three backpacks, otherwise the limit does not exist.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import gear, world
from src.models.identity import Body
from src.models.inventory import Item

BACKPACK = "simple_backpack"
EXO = "exoskeleton"
BASKET = "basket"
SACK = "sack"
BATTERY = "battery"
BIOPRINTER = "bioprinter"
TERMINAL = "market_terminal"
INGOT = "iron_ingot"


async def _body(session: AsyncSession):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.gear.{stamp}", "Узел", area_m2=100)
    identity = await world.create_identity(session, f"Носильщик-{stamp}")
    body = await world.print_body(session, identity, node)
    return node, identity, body


async def _give(session: AsyncSession, body, what: str, qty: float = 1):
    pocket = await world.body_container(session, body)
    return await world.grant_item(session, pocket, what, amount=qty, quality=60, origin="тест")


# --- mass --------------------------------------------------------------------


def test_mass_comes_from_data(catalog: Catalog) -> None:
    """Weight is vault content, not a number in code (D-065, D-146)."""
    book = catalog.recipes
    assert book.mass_of(BACKPACK) > 0
    assert book.mass_of(EXO) > book.mass_of(BACKPACK), "экзоскелет тяжелее рюкзака"
    #: An unknown name has no mass: the hole must be visible, not zeroed.
    assert book.mass_of("Философский камень") == 0


async def test_load_counts_everything_including_worn(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A pack does not become weightless because it is put on -- it rides in
    itself, lightened by its own factor like everything it holds (D-268)."""
    _, _, body = await _body(session)
    backpack = await _give(session, body, BACKPACK)
    await gear.equip(session, constants, catalog, body, backpack)

    carries = await gear.load_of(session, constants, catalog, body)
    factor = constants[R.INVENTORY_PACK][BACKPACK]["factor"]
    assert carries == pytest.approx(catalog.recipes.mass_of(BACKPACK) * factor)


# --- limit -------------------------------------------------------------------


async def test_no_more_than_limit_taken_in_hands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Not an error message but the reason wagons exist."""
    _, _, body = await _body(session)
    limit = constants[R.INVENTORY_CARRY_MASS]
    stone = "stone"
    qty = limit / catalog.recipes.mass_of(stone) + 1

    with pytest.raises(gear.Overloaded):
        await gear.check_carry(session, constants, catalog, body, stone, qty)


async def test_only_the_worn_pack_lightens(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A pack lightens what it holds (D-268), and only the pack on the back:
    a basket in the pocket lightens nothing, and a sack put on over it swaps
    the pack rather than stacking two. The numbers are the vault's, written
    out: a basket holds 8 kg at ×0.9, a sack 15 kg at ×0.85."""
    _, _, body = await _body(session)
    packs = constants[R.INVENTORY_PACK]
    assert packs[BASKET] == {"capacity": 8, "factor": 0.9}
    assert packs[SACK] == {"capacity": 15, "factor": 0.85}
    assert catalog.recipes.mass_of(INGOT) == 1.0 and catalog.recipes.mass_of(BASKET) == 0.3
    assert catalog.recipes.mass_of(SACK) == pytest.approx(0.12)

    await _give(session, body, INGOT, 10)
    assert await gear.load_of(session, constants, catalog, body) == pytest.approx(10.0)

    basket = await _give(session, body, BASKET)
    assert await gear.load_of(session, constants, catalog, body) == pytest.approx(10.3), (
        "корзина в руках -- просто вещь"
    )
    await gear.equip(session, constants, catalog, body, basket)
    #: Eight kilograms ride in the basket at nine tenths, the rest as they are.
    assert await gear.load_of(session, constants, catalog, body) == pytest.approx(8 * 0.9 + 2.3)

    sack = await _give(session, body, SACK)
    await gear.equip(session, constants, catalog, body, sack)
    #: The sack takes the basket's place: 10.42 kg all fit in its fifteen.
    assert await gear.load_of(session, constants, catalog, body) == pytest.approx(10.42 * 0.85), (
        "второй мешок на спину не надевается поверх первого"
    )
    #: The pack raises no limit: that is the exoskeleton's business.
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(
        constants[R.INVENTORY_CARRY_MASS]
    )


async def _charged(session: AsyncSession, body, charge: float):
    cell = await _give(session, body, BATTERY)
    cell.charge = Decimal(str(charge))
    cell.charged_at = datetime.now(UTC)
    await session.flush()
    return cell


async def test_exoskeleton_lifts_only_on_a_charged_battery(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The frame lifts while a charged cell rides in the hands (D-268); drained,
    it is a frame that weighs and lifts nothing."""
    _, _, body = await _body(session)
    base = constants[R.INVENTORY_CARRY_MASS]
    lift = constants[R.INVENTORY_EXO_BONUS][EXO]
    exo = await _give(session, body, EXO)
    await gear.equip(session, constants, catalog, body, exo)
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(base), (
        "без аккумулятора экзоскелет ничего не поднимает"
    )
    cell = await _charged(session, body, 20)
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(base + lift)
    cell.charge = Decimal(0)
    await session.flush()
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(base)


async def test_the_tick_drinks_the_battery_in_the_hands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Every worn exoskeleton drinks by the hour; a frame in the pocket does not."""
    _, _, body = await _body(session)
    exo = await _give(session, body, EXO)
    cell = await _charged(session, body, 20)
    moment = datetime.now(UTC)
    rate = constants[R.GEAR_EXO_ENERGY_PER_HOUR]

    drunk = await gear.wear_exoskeletons(session, constants, catalog, hours=1, now=moment)
    assert drunk == 0, "в руках, не на теле -- не пьёт"
    await gear.equip(session, constants, catalog, body, exo)
    drunk = await gear.wear_exoskeletons(session, constants, catalog, hours=1, now=moment)
    assert drunk == pytest.approx(rate)
    assert float(cell.charge) == pytest.approx(20 - rate)
    #: Drained to the bottom: takes what there is, and the lift is gone.
    drunk = await gear.wear_exoskeletons(session, constants, catalog, hours=100, now=moment)
    assert drunk == pytest.approx(20 - rate)
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(
        constants[R.INVENTORY_CARRY_MASS]
    )


async def test_two_drains_at_once_never_take_more_than_the_cell_holds(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Charge is a remainder (CLAUDE.md): two ticks over one cell must not both
    find it full."""
    from conftest import _slow
    from src.engine import battery

    _slow(monkeypatch, battery, "settle_charge")
    _, _, body = await _body(session)
    cell = await _charged(session, body, 5)
    body_id, cell_id = body.id, cell.id
    await session.commit()

    async def drain() -> float:
        async with factory() as db, db.begin():
            who = await db.get(Body, body_id)
            assert who is not None
            return await battery.drain_carried(db, constants, who, 4)

    taken = await asyncio.gather(drain(), drain())
    #: A hair below five: the cell leaked for the milliseconds between the
    #: charging and the draining (`energy.battery_selfdischarge`).
    assert sum(taken) == pytest.approx(5, abs=0.01), taken
    async with factory() as db:
        again = await db.get(Item, cell_id)
        assert again is not None and float(again.charge) == pytest.approx(0, abs=0.01)


async def test_a_station_built_in_place_stands_where_it_is_made_and_stays(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A bioprinter never enters the hands (D-268): printed, it stands on the
    floor; and nobody takes it up."""
    from src.engine import alpha, station, storage, world

    node, _, body = await _body(session)
    assert catalog.recipes.built(BIOPRINTER)
    printed = await alpha.spawn(session, constants, catalog, body, type_key=BIOPRINTER)
    yard = await world.node_container(session, node)
    assert printed.container_id == yard.id, "встал на пол, а не в руки"
    with pytest.raises(station.StationError):
        await station.take(session, catalog, body, printed)
    #: The second door (D-232 closed it for relics): the ground refuses too.
    with pytest.raises(storage.StorageError):
        await storage.pick(session, constants, catalog, body, printed)


async def test_a_batch_stands_a_built_station_on_the_floor(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The master at the bench takes every other yield into the hands; a built
    station stands where it was made (D-268)."""
    from src.engine import craft, jobs, world

    async with factory() as session, session.begin():
        node, identity, body = await _body(session)
        yard = await world.node_container(session, node)
        await world.grant_item(session, yard, "workbench", quality=60, origin="тест")
        await world.learn(session, identity, TERMINAL)
        for name, qty in catalog.recipes.recipe(TERMINAL).amounts.items():
            await _give(session, body, catalog.recipes.resolve(name), qty + 2)
        batch = await craft.start(session, constants, catalog, body, TERMINAL, 1)
        ready, yard_id, body_id = batch.ready_at, yard.id, body.id

    assert await jobs.run_one(factory, now=ready) is not None

    async with factory() as session:
        made = (
            (await session.execute(select(Item).where(Item.type_key == TERMINAL))).scalars().all()
        )
        assert [one.container_id for one in made] == [yard_id], "встал на пол у станка"
        who = await session.get(Body, body_id)
        assert who is not None and who.node_id is not None


# --- slots -------------------------------------------------------------------


async def test_one_slot_per_thing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Otherwise the player wears three backpacks and the limit no longer exists."""
    _, _, body = await _body(session)
    first = await _give(session, body, BACKPACK)
    second = await _give(session, body, BACKPACK)

    await gear.equip(session, constants, catalog, body, first)
    await gear.equip(session, constants, catalog, body, second)

    worn = await gear.equipped(session, body)
    assert list(worn) == ["back"], "в слоте одна вещь"
    assert worn["back"].id == second.id, "новая вытеснила прежнюю"
    #: A pack raises no limit (D-268): the one on the back changes the load, not the cap.
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(
        constants[R.INVENTORY_CARRY_MASS]
    ), "два рюкзака не складываются"


async def test_sack_and_basket_share_one_slot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One back: put on the sack -- took off the basket."""
    _, _, body = await _body(session)
    basket = await _give(session, body, BASKET)
    sack = await _give(session, body, SACK)

    await gear.equip(session, constants, catalog, body, basket)
    await gear.equip(session, constants, catalog, body, sack)
    worn = await gear.equipped(session, body)
    assert worn["back"].type_key == SACK


async def test_non_gear_not_wearable(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The slot comes from data: a pickaxe has none, and it cannot be worn."""
    _, _, body = await _body(session)
    pickaxe = await _give(session, body, "iron_pickaxe")
    with pytest.raises(gear.NotGear):
        await gear.equip(session, constants, catalog, body, pickaxe)


async def test_removed_stops_raising_limit(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _body(session)
    backpack = await _give(session, body, BACKPACK)
    await gear.equip(session, constants, catalog, body, backpack)
    removed = await gear.unequip(session, body, "back")

    assert removed is not None and removed.id == backpack.id
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(
        constants[R.INVENTORY_CARRY_MASS]
    )


async def test_charging_and_the_tick_do_not_lose_each_other(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tick drinks from the cell a player is charging at the counter: both
    write `charge`, and without the cell's row lock the later write would
    silently undo the earlier one."""
    from decimal import Decimal as D

    from conftest import _slow
    from src.engine import battery, energy, world
    from src.models.world import Layer

    _slow(monkeypatch, battery, "settle_charge")
    stamp = uuid.uuid4().hex[:8]
    city = await world.create_node(
        session, f"terra.cells.{stamp}", "Город", area_m2=1, layer=Layer.PLANET
    )
    yard = await world.create_node(
        session, f"terra.cells.{stamp}.yard", "Двор", area_m2=200, layer=Layer.CITY, parent=city
    )
    pool = await energy.pool_of(session, constants, yard)
    assert pool is not None
    pool.stored = D("1000")
    pool.tariff = D(0)
    pool.counted_at = datetime.now(UTC)
    who = await world.create_identity(session, f"Носильщик-{stamp}")
    body = await world.print_body(session, who, yard)
    exo = await _give(session, body, EXO)
    await gear.equip(session, constants, catalog, body, exo)
    cell = await _charged(session, body, 50)
    body_id, cell_id = body.id, cell.id
    await session.commit()

    async def charge() -> None:
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            item = await db.get(Item, cell_id)
            assert me is not None and item is not None
            await battery.charge_battery(db, constants, me, item, 10)

    async def drink() -> None:
        async with factory() as db, db.begin():
            await gear.wear_exoskeletons(db, constants, catalog, hours=1, now=datetime.now(UTC))

    await asyncio.gather(charge(), drink())
    async with factory() as db:
        again = await db.get(Item, cell_id)
        assert again is not None
        rate = constants[R.GEAR_EXO_ENERGY_PER_HOUR]
        assert float(again.charge) == pytest.approx(50 + 10 - rate, abs=0.05), (
            "и зарядка, и глоток тика остались в ячейке"
        )
