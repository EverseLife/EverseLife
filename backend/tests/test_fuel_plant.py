# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The fuel plant's pile (D-189, D-342).

The fuel lying where a fuel plant stands is the plant's bunker, not the yard's
store: anyone may pour into it, and neither the hand, nor a work, nor an
automat takes it back -- one answer for all three (`fuel_plant.off_the_pile`).
The work's reach has its own test (`test_reach.py`); the hand and the machine
are here.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from automat_kit import IRON, LUBRICANT, _factory_floor, _lube_in
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import automat, fuel_plant, liquid, storage, world
from src.units import amount_float


async def test_a_machine_by_a_fuel_plant_does_not_eat_its_pile(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The fuel lying where a fuel plant stands is the plant's tank (D-189),
    and a machine standing on it does not eat it either (D-342): the smelter
    stands for want of coal with the coal at its feet. The plant taken down,
    the pile is a yard's coal again, and the same smelter works it."""
    node, yard, _, body, _ = await _factory_floor(session, constants)
    smelter = await world.grant_item(session, yard, "auto_furnace", quality=70, origin="test")
    plant = await world.grant_item(session, yard, "coal_plant", quality=60, origin="test")
    await world.grant_item(session, yard, "iron_ore", amount=100, quality=60, origin="test")
    coal = await world.grant_item(session, yard, "coal", amount=100, quality=60, origin="test")
    await _lube_in(session, yard, 100)
    row = await automat.program(session, constants, catalog, body, smelter, IRON)
    moment = row.counted_at + timedelta(hours=2)

    made = await automat.advance(session, constants, row, catalog=catalog, now=moment)

    assert made == 0, "the plant's pile feeds the plant alone"
    assert row.counted_at == moment, "the hours went by, standing"
    await session.refresh(coal)
    assert amount_float(coal.amount) == pytest.approx(100), "the pile untouched"
    #: Barred is the fuel and only off the pile: the ore lying beside it and
    #: the lubricant in its canister are the machine's as before.
    taken = await liquid.locked_stacks(
        session,
        catalog,
        yard,
        ("iron_ore", "coal", LUBRICANT),
        barred=await fuel_plant.off_the_pile(session, constants, node),
    )
    assert {stack.type_key for stack in taken} == {"iron_ore", LUBRICANT}

    plant.installed = False
    await session.flush()
    made = await automat.advance(
        session, constants, row, catalog=catalog, now=moment + timedelta(hours=2)
    )
    assert made > 0, "no plant stands, so the coal is the yard's"


async def test_the_hand_takes_nothing_off_the_pile_but_what_is_not_fuel(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The hand is refused the plant's coal (D-189) and given the ore beside it:
    the bar is the fuel off the pile, not the yard."""
    node, yard, _, body, _ = await _factory_floor(session, constants)
    await world.grant_item(session, yard, "coal_plant", quality=60, origin="test")
    coal = await world.grant_item(session, yard, "coal", amount=10, quality=60, origin="test")
    ore = await world.grant_item(session, yard, "iron_ore", amount=10, quality=60, origin="test")

    with pytest.raises(storage.StorageError) as refused:
        await storage.pick(session, constants, catalog, body, coal, 1)
    assert refused.value.key == "storage-station-fuel"
    assert await storage.pick(session, constants, catalog, body, ore, 1) == pytest.approx(1)
    assert await fuel_plant.off_the_pile(session, constants, node) == frozenset(
        constants[R.ENERGY_FUEL_ENERGY]
    )
