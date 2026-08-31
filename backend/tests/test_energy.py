# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Energy: production, city pool, batteries, tariff (D-071, D-082, D-085).

Checked is what energy was made a separate system for:

* one pool per city, and only a city has one -- outside it one works from a battery;
* stations produce by time and without players; the coal one is dead without coal;
* energy does not lie in a sack: only the pool or a battery, and that one self-discharges;
* release is at the tariff, money goes to the city treasury: there is no free energy.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import energy, ledger, world
from src.models.ledger import AccountKind
from src.models.world import Layer
from src.units import PERCENT, money_str


async def _city(session: AsyncSession, *, river: bool = False):
    """A city: a planet delegate node and one built-up node under it."""
    stamp = uuid.uuid4().hex[:8]
    capital = await world.create_node(
        session, f"terra.city.{stamp}", "Столица", area_m2=1, layer=Layer.PLANET
    )
    yard = await world.create_node(
        session,
        f"terra.city.{stamp}.yard",
        "Двор",
        area_m2=200,
        layer=Layer.CITY,
        parent=capital,
        properties={"water": "river" if river else "нет"},
    )
    identity = await world.create_identity(session, f"Житель-{stamp}")
    body = await world.print_body(session, identity, yard)
    return capital, yard, identity, body


async def _place(session: AsyncSession, node, what: str, qty=1, quality=60):
    yard = await world.node_container(session, node)
    return await world.grant_item(session, yard, what, amount=qty, quality=quality, origin="тест")


# --- pool --------------------------------------------------------------------


async def test_pool_exists_in_city_and_not_outside(
    session: AsyncSession, constants: Constants
) -> None:
    """Outside a city there is no infrastructure: there one works from a brought battery."""
    _, yard, _, _ = await _city(session)
    assert await energy.pool_of(session, constants, yard) is not None

    wild = await world.create_node(
        session,
        f"terra.wild.{uuid.uuid4().hex[:6]}",
        "Пустошь",
        area_m2=100,
        layer=Layer.PLANET,
    )
    assert await energy.pool_of(session, constants, wild) is None


async def test_one_pool_per_city(session: AsyncSession, constants: Constants) -> None:
    """Inside a city energy is not routed anywhere: the balance is shared (D-071)."""
    capital, yard, _, _ = await _city(session)
    second = await world.create_node(
        session,
        f"{yard.key}.2",
        "Второй двор",
        area_m2=100,
        layer=Layer.CITY,
        parent=capital,
    )
    one = await energy.pool_of(session, constants, yard)
    other = await energy.pool_of(session, constants, second)
    assert one is not None and other is not None
    assert one.id == other.id


# --- production --------------------------------------------------------------


async def test_water_wheel_works_only_by_river(session: AsyncSession, constants: Constants) -> None:
    """Geography decides: wheels tie early cities to rivers."""
    _, by_river, _, _ = await _city(session, river=True)
    _, in_steppe, _, _ = await _city(session, river=False)
    await _place(session, by_river, energy.WHEEL)
    await _place(session, in_steppe, energy.WHEEL)

    moment = datetime.now(UTC)
    riverside = await energy.pool_of(session, constants, by_river)
    steppe = await energy.pool_of(session, constants, in_steppe)
    riverside.counted_at = moment - timedelta(hours=1)
    steppe.counted_at = moment - timedelta(hours=1)

    river_yield = await energy.produce(session, constants, riverside, now=moment)
    steppe_yield = await energy.produce(session, constants, steppe, now=moment)

    assert river_yield == pytest.approx(constants[R.ENERGY_WATERWHEEL_RATE], rel=0.01)
    assert steppe_yield == 0


async def test_coal_station_burns_coal_and_dead_without_it(
    session: AsyncSession, constants: Constants
) -> None:
    """A station without coal is dead -- hence the energy blockade (D-082)."""
    from sqlalchemy import select

    from src.models.inventory import Item
    from src.units import amount_float

    _, yard, _, _ = await _city(session)
    await _place(session, yard, "coal_plant")
    await _place(session, yard, "coal", qty=100)

    moment = datetime.now(UTC)
    pool = await energy.pool_of(session, constants, yard)
    pool.counted_at = moment - timedelta(hours=2)
    yielded = await energy.produce(session, constants, pool, now=moment)

    burned = constants[R.ENERGY_COAL_PLANT_FUEL_DRAW] * 2
    #: Energy per unit is a property of the material now (D-215).
    per_coal = constants[R.ENERGY_FUEL_ENERGY]["coal"]
    assert yielded == pytest.approx(burned * per_coal, rel=0.01)
    #: The vault rate matches the draw: 4 coal per hour give 200 energy.
    assert yielded == pytest.approx(constants[R.ENERGY_COAL_PLANT_RATE] * 2, rel=0.01)

    container = await world.node_container(session, yard)
    left = sum(
        amount_float(i_.amount)
        for i_ in (
            await session.execute(
                select(Item).where(Item.container_id == container.id, Item.type_key == "coal")
            )
        )
        .scalars()
        .all()
    )
    assert left == pytest.approx(100 - burned)

    #: The coal ran out -- the station stopped.
    for stack in (
        (
            await session.execute(
                select(Item).where(Item.container_id == container.id, Item.type_key == "coal")
            )
        )
        .scalars()
        .all()
    ):
        await session.delete(stack)
    await session.flush()
    pool.counted_at = moment
    assert await energy.produce(session, constants, pool, now=moment + timedelta(hours=5)) == 0


async def test_windmill_unstable_within_vault_bounds(
    session: AsyncSession, constants: Constants
) -> None:
    """ "Depends on weather" is all the vault says about wind."""
    _, yard, _, _ = await _city(session)
    await _place(session, yard, energy.WINDMILL)
    wind = constants[R.ENERGY_WINDMILL_RATE]

    moment = datetime.now(UTC)
    pool = await energy.pool_of(session, constants, yard)
    for attempt in range(5):
        pool.counted_at = moment - timedelta(hours=1)
        yielded = await energy.produce(
            session, constants, pool, now=moment, rng=random.Random(attempt)
        )
        assert wind.min <= yielded <= wind.max


# --- battery and tariff ------------------------------------------------------


async def test_charging_takes_from_pool_and_pays_treasury(
    session: AsyncSession, constants: Constants
) -> None:
    """There is no free energy: zero is a tariff too (D-085)."""
    capital, yard, identity, body = await _city(session)
    pool = await energy.pool_of(session, constants, yard)
    pool.stored = Decimal("400")
    pool.counted_at = datetime.now(UTC)

    pocket = await world.body_container(session, body)
    battery = await world.grant_item(session, pocket, energy.BATTERY, quality=55, origin="тест")
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    from src.models.ledger import PostingReason
    from src.units import money

    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=genesis.id,
        credit=account.id,
        amount=money(100),
        memo={},
    )

    given = await energy.charge_battery(session, constants, body, battery, 200)
    assert given == pytest.approx(200)
    assert float(pool.stored) == pytest.approx(200)
    assert float(battery.charge) == pytest.approx(200)

    treasury = await ledger.account_for(session, AccountKind.CITY_TREASURY, pool.node_id)
    #: The tariff is given per hundred energy: two hundred -- two tariffs.
    expected = money(2 * constants[R.ENERGY_TARIFF_DEFAULT])
    assert await ledger.balance(session, treasury.id) == expected
    assert money_str(await ledger.balance(session, account.id)) == money_str(money(100) - expected)


async def test_nowhere_to_charge_outside_city(session: AsyncSession, constants: Constants) -> None:
    stamp = uuid.uuid4().hex[:6]
    wild = await world.create_node(
        session, f"terra.far.{stamp}", "Застава", area_m2=100, layer=Layer.PLANET
    )
    identity = await world.create_identity(session, f"Путник-{stamp}")
    body = await world.print_body(session, identity, wild)
    pocket = await world.body_container(session, body)
    battery = await world.grant_item(session, pocket, energy.BATTERY, quality=55, origin="тест")
    with pytest.raises(energy.NoGrid):
        await energy.charge_battery(session, constants, body, battery)


async def test_battery_self_discharges(session: AsyncSession, constants: Constants) -> None:
    """Energy cannot be stockpiled: it is a perishable commodity.

    A day here is planetary -- `time.day_terra`, the same as for the plot and
    sleep: every planet has its own (D-008), and there is no second day in the world.
    """
    _, yard, _, body = await _city(session)
    pocket = await world.body_container(session, body)
    battery = await world.grant_item(session, pocket, energy.BATTERY, quality=55, origin="тест")
    moment = datetime.now(UTC)
    battery.charge = Decimal("500")
    battery.charged_at = moment
    await session.flush()

    day = timedelta(hours=constants[R.TIME_DAY_TERRA])
    in_a_day = energy.charge_of(constants, battery, now=moment + day)
    leak = (
        constants[R.ENERGY_BATTERY_CAPACITY] * constants[R.ENERGY_BATTERY_SELFDISCHARGE] / PERCENT
    )
    assert in_a_day == pytest.approx(500 - leak)


async def test_energy_does_not_sit_in_sack(session: AsyncSession, constants: Constants) -> None:
    """Only the pool or a battery -- there is no third kind of storage (D-071)."""
    _, yard, _, body = await _city(session)
    pocket = await world.body_container(session, body)
    sack = await world.grant_item(session, pocket, "shaft_support", quality=50, origin="тест")
    with pytest.raises(energy.NotBattery):
        await energy.charge_battery(session, constants, body, sack)


# --- battery as a machine (D-179) --------------------------------------------


async def test_battery_is_machine(catalog) -> None:
    """Placed in a building like every machine: there is no separate item kind."""
    from src.engine import station

    assert station.is_station(catalog, energy.BATTERY)


async def test_placed_battery_charges(session: AsyncSession, constants: Constants) -> None:
    """Charge is taken both into hands and into the house: a battery is property of the place
    (D-179)."""
    _, yard, identity, body = await _city(session)
    pool = await energy.pool_of(session, constants, yard)
    pool.stored = Decimal("400")
    pool.counted_at = datetime.now(UTC)

    #: Stands in the node, not in the pocket -- like a placed machine.
    battery = await _place(session, yard, energy.BATTERY, quality=55)
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    from src.models.ledger import PostingReason
    from src.units import money

    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=genesis.id,
        credit=account.id,
        amount=money(100),
        memo={},
    )

    given = await energy.charge_battery(session, constants, body, battery, 150)
    assert given == pytest.approx(150)
    assert float(battery.charge) == pytest.approx(150)


async def test_foreign_battery_in_other_node_not_chargeable(
    session: AsyncSession, constants: Constants
) -> None:
    """In-person stays in-person: one cannot reach across the city."""
    _, yard, _, body = await _city(session)
    pool = await energy.pool_of(session, constants, yard)
    pool.stored = Decimal("400")
    pool.counted_at = datetime.now(UTC)

    _, adjacent, _, _ = await _city(session)
    foreign_ = await _place(session, adjacent, energy.BATTERY, quality=55)

    with pytest.raises(energy.BatteryError):
        await energy.charge_battery(session, constants, body, foreign_)
