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
from src.engine import battery as battery_
from src.engine import energy, ledger, world
from src.models.energy import EnergyPool
from src.models.inventory import Item
from src.models.ledger import AccountKind
from src.models.world import Layer
from src.units import PERCENT, ROUND_CHARGE, ROUND_ENERGY, amount, money, money_str


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


def test_the_pool_is_kept_at_the_scale_it_is_written_with() -> None:
    """`ROUND_ENERGY` and the column's scale are one number in two places.

    Widening `Numeric(14, 3)` alone would leave the forced step ten times the
    column's own, and every draw too thin to write would cost the city ten
    times over, in silence.
    """
    assert EnergyPool.__table__.c.stored.type.scale == ROUND_ENERGY


async def test_a_pour_too_thin_to_write_is_refused_not_served(
    session: AsyncSession, constants: Constants
) -> None:
    """The door refuses what the column cannot hold, instead of burning the pool.

    A positive draw always moves the pool by a whole step. Asked for less than
    one, the city would pay that step while the cell gained nothing and the
    bill rounded to nothing: free, and repeatable by anyone standing at a
    charger.
    """
    capital, yard, identity, body = await _city(session)
    pool = await energy.pool_of(session, constants, yard)
    pool.stored = Decimal("100")
    pool.counted_at = datetime.now(UTC)
    pocket = await world.body_container(session, body)
    cell = await world.grant_item(session, pocket, energy.BATTERY, quality=55, origin="тест")
    await session.flush()

    before = Decimal(pool.stored)
    with pytest.raises(energy.NotEnough):
        await energy.charge_battery(session, constants, body, cell, 0.0004)
    assert Decimal(pool.stored) == before


async def test_settling_often_does_not_stop_the_leak(
    session: AsyncSession, constants: Constants
) -> None:
    """A cell settled every second still leaks over the hour.

    The charge is kept to a thousandth, so a leak thinner than that cannot be
    written -- and the stamp used to move regardless, throwing those seconds
    away. Every command touching a cell settles it, so a cell in steady use
    never leaked at all.
    """
    capital, yard, identity, body = await _city(session)
    pocket = await world.body_container(session, body)
    cell = await world.grant_item(session, pocket, energy.BATTERY, quality=55, origin="тест")
    started = datetime.now(UTC)
    cell.charge = Decimal("400")
    cell.charged_at = started
    await session.flush()

    #: Settled once a second across an hour, then read.
    for tick in range(3600):
        await energy.settle_charge(session, constants, cell, now=started + timedelta(seconds=tick))
    settled_often = float(cell.charge)

    #: The same hour, left alone and settled once.
    other = await world.grant_item(session, pocket, energy.BATTERY, quality=55, origin="тест")
    other.charge = Decimal("400")
    other.charged_at = started
    await session.flush()
    settled_once = await energy.settle_charge(
        session, constants, other, now=started + timedelta(hours=1)
    )

    #: Within the thousandth the column can tell apart.
    assert settled_often == pytest.approx(settled_once, abs=0.001)
    #: And the hour really did cost something, or the test proves nothing.
    assert settled_once < 400


async def test_the_bill_is_for_what_the_cell_actually_holds(
    session: AsyncSession, constants: Constants
) -> None:
    """Payer, pool, cell and journal say one number.

    The bill used to be issued on the asked-for figure while the row kept the
    rounded one, so the payer and the cell disagreed by up to half a
    thousandth in whichever direction the rounding fell.
    """
    capital, yard, identity, body = await _city(session)
    pool = await energy.pool_of(session, constants, yard)
    pool.stored = Decimal("400")
    pool.counted_at = datetime.now(UTC)
    pocket = await world.body_container(session, body)
    cell = await world.grant_item(session, pocket, energy.BATTERY, quality=55, origin="тест")
    cell.charge = Decimal("0")
    cell.charged_at = datetime.now(UTC)
    await session.flush()

    from src.models.ledger import PostingReason

    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=genesis.id,
        credit=account.id,
        amount=money(100),
        memo={},
    )

    #: An amount that does not sit on the column's grid.
    given = await energy.charge_battery(session, constants, body, cell, 1.0015)
    await session.flush()
    await session.refresh(cell)
    assert float(cell.charge) == pytest.approx(given)


async def test_the_pool_never_hands_out_energy_it_kept(
    session: AsyncSession, constants: Constants
) -> None:
    """A draw too thin to write is not a free draw, for the pool either.

    `EnergyPool.stored` keeps a thousandth, and the taker is billed before the
    row is written: a draw under half of that used to round back to the figure
    the pool already held, so the energy was spent and the pool stayed full,
    command after command.
    """
    capital, yard, identity, body = await _city(session)
    pool = await energy.pool_of(session, constants, yard)
    pool.stored = Decimal("100")
    pool.counted_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(pool)

    before = Decimal(pool.stored)
    draws, each = 20, 0.0004
    for _ in range(draws):
        energy.take_from_pool(pool, each)
        await session.flush()
        await session.refresh(pool)

    #: The row actually moved: the pool paid for every draw it served.
    assert Decimal(pool.stored) < before
    #: And it never paid out more than a step per draw.
    assert float(before - Decimal(pool.stored)) <= draws * 0.001


async def test_a_stack_never_hands_out_charge_it_did_not_lose(
    session: AsyncSession, constants: Constants
) -> None:
    """A draw too thin to write is not a free draw.

    The charge column keeps a thousandth of one cell, and a stack is drained
    evenly, so a draw of less than that per cell used to round back to the
    charge it started with: the caller was told it got the energy and the row
    kept everything. A thousand cells made every draw under half a unit free,
    over and over, a command at a time.
    """
    capital, yard, identity, body = await _city(session)
    pocket = await world.body_container(session, body)
    pile = 1000
    stack = await world.grant_item(session, pocket, energy.BATTERY, quality=55, origin="тест")
    #: A thousand cells standing as one stack (D-179): the charge column is one
    #: cell's, and the amount says how many there are.
    stack.amount = amount(pile)
    stack.charge = Decimal("10")
    #: One moment throughout: self-discharge is not what is under test.
    moment = datetime.now(UTC)
    stack.charged_at = moment
    await session.flush()

    #: Read back from the row, not from the object: the rounding that hid this
    #: happens on the way into Postgres, so a draw that never leaves the
    #: session shows nothing. Each pass here is a command of its own.
    await session.refresh(stack)
    before = Decimal(stack.charge)
    draws, each = 20, 0.4
    taken = 0.0
    for _ in range(draws):
        taken += await battery_.drain_cells(session, constants, [stack], each, now=moment)
        await session.flush()
        await session.refresh(stack)
    left_in_cells = float((before - Decimal(stack.charge)) * pile)

    #: The cells gave up at least what the caller was handed. Backwards, this
    #: is the free draw: energy delivered out of a stack that never emptied.
    assert taken <= left_in_cells
    #: And no more than was asked for. Forwards, this is the other half: the
    #: stack loses a whole step per cell, and handing that overshoot to the
    #: caller would let `automat` bill it as hours the clock never allowed.
    assert taken == pytest.approx(draws * each)
    #: And the stack actually emptied: it is not that nothing was drawn.
    assert Decimal(stack.charge) < before


def test_the_charge_is_kept_at_the_scale_it_is_written_with() -> None:
    """`ROUND_CHARGE` and the column's scale are one number in two places.

    Widening `Numeric(12, 3)` alone would leave the drain measuring against a
    grid the row no longer keeps, and the free draw would be back.
    """
    assert Item.__table__.c.charge.type.scale == ROUND_CHARGE


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


async def test_fuel_plant_burns_petroleum_coke_too(
    session: AsyncSession, constants: Constants
) -> None:
    """What burns is data (D-215): the coke of D-252 carries `fuel: 90`, and
    the same coal plant eats it with no engine change -- denser than coal,
    so the same draw yields nearly twice the energy."""
    _, yard, _, _ = await _city(session)
    await _place(session, yard, "coal_plant")
    await _place(session, yard, "petroleum_coke", qty=100)

    moment = datetime.now(UTC)
    pool = await energy.pool_of(session, constants, yard)
    pool.counted_at = moment - timedelta(hours=2)
    yielded = await energy.produce(session, constants, pool, now=moment)

    burned = constants[R.ENERGY_COAL_PLANT_FUEL_DRAW] * 2
    per_coke = constants[R.ENERGY_FUEL_ENERGY]["petroleum_coke"]
    assert per_coke > constants[R.ENERGY_FUEL_ENERGY]["coal"], "кокс плотнее угля"
    assert yielded == pytest.approx(burned * per_coke, rel=0.01)
