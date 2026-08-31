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

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import gear, world

BACKPACK = "simple_backpack"
EXO = "exoskeleton"
BASKET = "basket"
SACK = "sack"


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
    """An exoskeleton does not become weightless because it is put on."""
    _, _, body = await _body(session)
    backpack = await _give(session, body, BACKPACK)
    await gear.equip(session, constants, catalog, body, backpack)

    carries = await gear.load_of(session, catalog, body)
    assert carries == pytest.approx(catalog.recipes.mass_of(BACKPACK))


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


async def test_only_the_worn_bag_counts(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The bonus follows what is on the back, not what is in the hands: a
    basket in the pocket adds nothing, and swapping it for a sack swaps the
    bonus rather than stacking it (D-146)."""
    _, _, body = await _body(session)
    base = await gear.capacity(session, constants, catalog, body)
    assert base == pytest.approx(constants[R.INVENTORY_CARRY_MASS])
    bonus = constants[R.INVENTORY_CARRY_BONUS]

    basket = await _give(session, body, BASKET)
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(base), (
        "корзина в руках переносимого не добавляет"
    )
    await gear.equip(session, constants, catalog, body, basket)
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(
        base + bonus[BASKET]
    )
    sack = await _give(session, body, SACK)
    await gear.equip(session, constants, catalog, body, sack)
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(
        base + bonus[SACK]
    ), "второй мешок на спину не надевается поверх первого"

    backpack = await _give(session, body, BACKPACK)
    await gear.equip(session, constants, catalog, body, backpack)
    bonuses = constants[R.INVENTORY_CARRY_BONUS]
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(
        base + bonuses[BACKPACK]
    )


async def test_exoskeleton_raises_more_than_backpack(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Capital in gear: the exoskeleton is the top of the carrying branch."""
    bonuses = constants[R.INVENTORY_CARRY_BONUS]
    assert bonuses[EXO] > bonuses[BACKPACK]

    _, _, body = await _body(session)
    backpack = await _give(session, body, BACKPACK)
    exo = await _give(session, body, EXO)
    await gear.equip(session, constants, catalog, body, backpack)
    await gear.equip(session, constants, catalog, body, exo)

    #: The slots differ -- back and frame -- so both work.
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(
        constants[R.INVENTORY_CARRY_MASS] + bonuses[BACKPACK] + bonuses[EXO]
    )


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
    bonuses = constants[R.INVENTORY_CARRY_BONUS]
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(
        constants[R.INVENTORY_CARRY_MASS] + bonuses[BACKPACK]
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
