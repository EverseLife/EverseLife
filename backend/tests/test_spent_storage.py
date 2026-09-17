# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A thing with something inside it is not material (D-344).

What a storage holds lies in a container of its own, tied to it by id and not
by a foreign key. A work that spent the storage deleted the row alone, and
its contents stayed behind in a place that no longer exists: the water in the
only clay pot of a battery left the world without a word, and so did the
canister laid out in a wrong guess. Checked here:

* a work does not take a storage holding anything -- the forecast and the
  start agree, and the refusal says the shortage is of empty ones;
* an emptied one is spent as before, even though its inside is still there;
* a failed invention burns what is laid out, but never a full vessel nor a
  loaded barrow -- a hold is an inside too;
* the automat, which gathers through its own door, keeps to the same rule.

The races with a pour landing in the pot around the write-off, in both
orders, are in `test_races_storage.py`.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from automat_kit import NAILS, _factory_floor, _learn, _lube_in
from src.constants import Catalog, Constants
from src.engine import automat, craft, transport, world
from src.models.inventory import Item
from src.units import amount_float
from storage_kit import (
    ACID,
    BARROW,
    BATTERY,
    CANISTER,
    LEAD,
    POT,
    _bench,
    _orphaned,
    _there,
    _vessel,
    _water_in,
)


async def _amount(session: AsyncSession, container_id, type_key: str) -> float:
    rows = await session.execute(
        select(Item.amount).where(Item.container_id == container_id, Item.type_key == type_key)
    )
    return sum(amount_float(value) for value in rows.scalars().all())


async def test_the_only_pot_with_water_in_it_is_not_spent_on_a_battery(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The pot is the battery's input, and it holds water: no battery.

    The forecast says so as well as the start (D-092), and in words that name
    the reason -- the master is holding a pot, and "not enough pots" would read
    as the world miscounting.
    """
    _, _, body, _ = await _bench(session)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, LEAD, amount=5, quality=60, origin="test")
    await world.grant_item(session, pocket, ACID, amount=5, quality=60, origin="test")
    pot = await _vessel(session, pocket, POT, water=2)

    with pytest.raises(craft.NotEnough) as forecast:
        await craft.plan(session, constants, catalog, body, BATTERY, 1)
    assert forecast.value.key == "craft-not-enough-empty"
    assert forecast.value.params["goods"] == POT

    with pytest.raises(craft.NotEnough) as refused:
        await craft.start(session, constants, catalog, body, BATTERY, 1)
    assert refused.value.key == "craft-not-enough-empty"

    assert await _there(session, pot.id), "the pot with water in it was not spent"
    assert await _water_in(session, pot.id) == pytest.approx(2)
    assert await _amount(session, pocket.id, LEAD) == pytest.approx(5)
    assert await _orphaned(session) == 0


async def test_the_empty_pot_goes_into_the_battery_and_the_full_one_stays(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Two pots within reach, and the full one is the worse: worst first
    (D-058) would take it, and took it -- with its water.

    The empty one has been poured out and keeps its inside: an empty container
    is not "something inside", and the pot is spent like any other. The
    largest batch counts one pot, not two, so the number the button fills in
    is the batch's.
    """
    _, _, body, yard = await _bench(session)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, LEAD, amount=5, quality=60, origin="test")
    await world.grant_item(session, pocket, ACID, amount=5, quality=60, origin="test")
    full = await _vessel(session, pocket, POT, water=3, quality=10)
    empty = await _vessel(session, yard, POT, water=0, quality=90)

    assert await craft.most(session, constants, catalog, body, BATTERY) == 1
    await craft.start(session, constants, catalog, body, BATTERY, 1)
    await session.flush()

    assert not await _there(session, empty.id), "the empty pot went into the battery"
    assert await _there(session, full.id), "the full one stayed, worse as it is"
    assert await _water_in(session, full.id) == pytest.approx(3)
    assert await _orphaned(session) == 0


async def test_a_wrong_guess_does_not_burn_a_full_canister(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A failed invention burns a share of what is laid out (D-209) -- a whole
    canister, rounded up -- and a canister of water burned left the water
    lying nowhere. Laid out, it is simply not there to lay: the attempt is
    refused before anything burns."""
    _, _, body, _ = await _bench(session)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "wood", amount=10, quality=60, origin="test")
    canister = await _vessel(session, pocket, CANISTER, water=4)

    with pytest.raises(craft.NotEnough) as refused:
        await craft.invent(
            session, constants, catalog, body, {CANISTER: 1, "wood": 5}, 1, station="workbench"
        )
    assert refused.value.key == "craft-not-enough-empty"

    assert await _there(session, canister.id)
    assert await _water_in(session, canister.id) == pytest.approx(4)
    assert await _amount(session, pocket.id, "wood") == pytest.approx(10)
    assert await _orphaned(session) == 0


async def test_a_wrong_guess_does_not_burn_a_loaded_barrow(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hold is an inside too (D-313): a barrow lying in the yard with nails in
    it is laid out and refused, as the canister is, rather than burned with the
    nails left riding in a hold nobody owns."""
    _, _, body, yard = await _bench(session)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "wood", amount=10, quality=60, origin="test")
    barrow = await world.grant_item(
        session, yard, BARROW, quality=60, origin="test", installed=False
    )
    hold = await transport.cargo(session, barrow)
    await world.grant_item(session, hold, "nails", amount=7, quality=60, origin="test")

    with pytest.raises(craft.NotEnough) as refused:
        await craft.invent(
            session, constants, catalog, body, {BARROW: 1, "wood": 5}, 1, station="workbench"
        )
    assert refused.value.key == "craft-not-enough-empty"

    assert await _there(session, barrow.id)
    assert await _amount(session, hold.id, "nails") == pytest.approx(7)
    assert await _orphaned(session) == 0


async def test_an_automat_does_not_spend_a_full_pot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The automat gathers its inputs through `liquid.locked_stacks`, not
    through the work's reach, and keeps to the same rule there.

    Programmed by hand: the programming door refuses a station among the
    outputs (`automat._programmable`, and a battery is a station), but the
    tick does not ask it again, and it must not spend a full vessel whatever
    recipe the vault gives one next. The second half is the control: with an
    empty pot beside it the machine does work -- so the first half stood for
    the water and not for want of energy or lubricant.
    """
    _, yard, identity, body, machine = await _factory_floor(session, constants)
    await world.grant_item(session, yard, LEAD, amount=10, quality=60, origin="test")
    await world.grant_item(session, yard, ACID, amount=10, quality=60, origin="test")
    await _lube_in(session, yard, 100)
    full = await _vessel(session, yard, POT, water=2)
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)
    row.recipe_key = BATTERY
    await session.flush()

    moment = row.counted_at + timedelta(hours=8)
    made = await automat.advance(session, constants, row, catalog=catalog, now=moment)
    assert made == 0, "no battery out of a full pot"
    assert await _there(session, full.id)
    assert await _water_in(session, full.id) == pytest.approx(2)
    assert await _amount(session, yard.id, LEAD) == pytest.approx(10)
    assert await _orphaned(session) == 0

    empty = await _vessel(session, yard, POT, water=0)
    made = await automat.advance(
        session, constants, row, catalog=catalog, now=moment + timedelta(hours=8)
    )
    assert made == 1, "with an empty pot beside it the machine works"
    assert not await _there(session, empty.id)
    assert await _there(session, full.id)
    assert await _water_in(session, full.id) == pytest.approx(2)
    assert await _orphaned(session) == 0
