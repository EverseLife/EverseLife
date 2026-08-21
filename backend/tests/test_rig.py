# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Drilling rig: capital instead of labour (D-115).

Checked is what the rig was introduced this way for:

* it works without the player and **does not sleep** -- that is its whole strength;
* and loses to a human in everything else: lower output, quality bounded by
  `rig.quality_cap`, eats the vein twice as fast;
* three obligations keep it dependent on people: fuel, hopper and maintenance.
  Any one violated -- and the machine stands.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import rig, world
from src.models.inventory import Item
from src.units import amount_float


async def _face(session: AsyncSession, *, coal: float = 100, richness: float = 60):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.pit.{stamp}", "Забой", area_m2=200)
    vein = await world.create_vein(
        session, node, "Железная руда", richness=richness, remaining=100_000
    )
    yard = await world.node_container(session, node)
    if coal > 0:
        await world.grant_item(
            session, yard, "Уголь", amount=coal, quality=55, origin="тест"
        )
    identity = await world.create_identity(session, f"Промышленник-{stamp}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    machine = await world.grant_item(
        session, pocket, "Буровая установка", quality=70, origin="тест"
    )
    installation = await rig.place(session, body, machine, vein)
    return node, vein, body, installation, machine


def _via(installation, hours: float) -> datetime:
    return installation.counted_at + timedelta(hours=hours)


# --- works without the player ------------------------------------------------


async def test_mines_over_time_and_burns_coal(
    session: AsyncSession, constants: Constants
) -> None:
    """The machine does not sleep: the hopper fills while the owner is busy elsewhere."""
    node, _, _, installation, machine = await _face(session)
    coal_before = 100.0

    mined = await rig.advance(session, constants, installation, now=_via(installation, 4))
    #: Output is set by the vault and does not depend on condition: a worn
    #: machine digs not less but worse -- that shows in quality on emptying.
    assert mined == pytest.approx(constants[R.RIG_OUTPUT_PER_HOUR] * 4)
    assert float(installation.hopper) == pytest.approx(mined)

    yard = await world.node_container(session, node)
    left = await rig._coal_available(session, yard.id)  # noqa: SLF001
    assert left == pytest.approx(
        coal_before - constants[R.RIG_FUEL_PER_HOUR] * 4, rel=0.01
    )


async def test_machine_loses_to_human_in_output(
    session: AsyncSession, constants: Constants
) -> None:
    """Craft is the way to get good ore, the rig -- a lot of average."""
    assert constants[R.RIG_OUTPUT_PER_HOUR] < constants[R.MINING_IRON_PER_HOUR]


# --- three obligations -------------------------------------------------------


async def test_rig_idle_without_coal(
    session: AsyncSession, constants: Constants
) -> None:
    """Fuel ran out -- it stopped. Hence a standing contract with a coal hauler."""
    _, _, _, installation, _ = await _face(session, coal=0)
    mined = await rig.advance(session, constants, installation, now=_via(installation, 5))
    assert mined == 0
    assert float(installation.hopper) == 0


async def test_coal_lasts_exactly_its_hours(
    session: AsyncSession, constants: Constants
) -> None:
    """An hour and a half of fuel -- an hour and a half of work, not five."""
    hours = 1.5
    coal = constants[R.RIG_FUEL_PER_HOUR] * hours
    _, _, _, installation, _ = await _face(session, coal=coal)

    mined = await rig.advance(session, constants, installation, now=_via(installation, 5))
    assert mined == pytest.approx(constants[R.RIG_OUTPUT_PER_HOUR] * hours, rel=0.01)


async def test_full_bunker_stops_machine(
    session: AsyncSession, constants: Constants
) -> None:
    """Coming is mandatory: without a carter the enterprise does not work."""
    _, _, _, installation, _ = await _face(session, coal=100_000)
    volume = rig.hopper_capacity(constants)

    #: Deliberately more than the hopper holds.
    hours = constants[R.RIG_HOPPER_CAPACITY] * 3
    await rig.advance(session, constants, installation, now=_via(installation, hours))
    assert float(installation.hopper) == pytest.approx(volume, rel=0.02)

    #: And it grows no further, however long one waits.
    more = await rig.advance(
        session, constants, installation, now=_via(installation, hours)
    )
    assert more == 0


async def test_rig_wears_and_abandoned_falls_apart(
    session: AsyncSession, constants: Constants
) -> None:
    """`rig.wear_per_day` goes by time, not by what is mined.

    A good machine wears slower -- by the same common rule as a pickaxe and an
    anvil (D-129): no second formula is created for the rig.
    """
    from src.engine import wear

    _, _, _, installation, machine = await _face(session)
    before = float(machine.condition)
    day = constants[R.TIME_DAY_TERRA]
    await rig.advance(session, constants, installation, now=_via(installation, day))

    term = wear.life_factor(constants, float(machine.quality))
    assert float(machine.condition) == pytest.approx(
        before - constants[R.RIG_WEAR_PER_DAY] / term, abs=0.01
    )


# --- emptying and quality ----------------------------------------------------


async def test_bunker_emptied_on_foot_and_quality_under_ceiling(
    session: AsyncSession, constants: Constants
) -> None:
    """The machine works by its setting: above `rig.quality_cap` it does not give."""
    _, vein, body, installation, _ = await _face(session, richness=80)
    #: An hour of work, not three: the hopper is emptied by hand, and hands are
    #: not bottomless (D-146). A full hopper is work for a carter, not for pockets.
    await rig.advance(session, constants, installation, now=_via(installation, 1))

    taken = await rig.empty_hopper(session, constants, body, installation)
    assert taken > 0
    assert float(installation.hopper) == 0

    pocket = await world.body_container(session, body)
    from sqlalchemy import select

    ore_ = (
        await session.execute(
            select(Item).where(
                Item.container_id == pocket.id, Item.type_key == vein.resource
            )
        )
    ).scalars().all()
    assert ore_, "бункер переехал в карман"
    quality = float(ore_[0].quality)
    assert quality == pytest.approx(constants[R.RIG_QUALITY_CAP])
    assert quality < 80, "богатая жила машине не помогает — она ровна по настройке"
    assert amount_float(ore_[0].amount) == pytest.approx(taken, rel=0.01)


async def test_broken_machine_gives_worse_ore(
    session: AsyncSession, constants: Constants
) -> None:
    """Maintenance is mandatory: a worn one does not break suddenly, it works worse."""
    _, _, body, installation, machine = await _face(session, richness=80)
    from decimal import Decimal

    machine.condition = Decimal("20")
    await session.flush()
    await rig.advance(session, constants, installation, now=_via(installation, 1))
    await rig.empty_hopper(session, constants, body, installation)

    from sqlalchemy import select

    pocket = await world.body_container(session, body)
    ore_ = (
        await session.execute(
            select(Item).where(Item.container_id == pocket.id, Item.type_key == "Железная руда")
        )
    ).scalars().all()
    quality = float(ore_[0].quality)
    assert quality < constants[R.RIG_QUALITY_CAP], "потолок опустился с износом"


async def test_foreign_bunker_not_emptied(
    session: AsyncSession, constants: Constants
) -> None:
    """Emptying is by contract with the owner, not by showing up (D-116)."""
    node, _, _, installation, _ = await _face(session)
    foreign_id = await world.create_identity(session, f"Чужой-{uuid.uuid4().hex[:6]}")
    foreign_body = await world.print_body(session, foreign_id, node)

    with pytest.raises(rig.NotYours):
        await rig.empty_hopper(session, constants, foreign_body, installation)


async def test_eats_vein_twice_as_fast(
    session: AsyncSession, constants: Constants
) -> None:
    """Capital speeds up the world's depletion -- and that is a reason for a dispute at the vein
    (D-101)."""
    _, vein, _, installation, _ = await _face(session)
    before = vein.remaining

    mined = await rig.advance(session, constants, installation, now=_via(installation, 4))
    went = amount_float(before - vein.remaining)
    assert went == pytest.approx(
        mined * constants[R.RIG_DEPLETION_MULTIPLIER], rel=0.01
    )
