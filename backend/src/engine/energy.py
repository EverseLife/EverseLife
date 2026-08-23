# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Energy: production, city pool, batteries, tariff (D-071, D-082, D-085).

Energy is not a backdrop but a commodity that any development runs into. It
is deliberately built to be inconvenient to hoard and convenient for politics:

* **produced** by a station, for fuel that somebody mined and hauled;
* **lives** either in the city pool or in a battery -- there is none in a sack;
* **released at a tariff**, and never free: zero is a tariff too.

## Where each formula came from

**Generation.** Rates are set by the vault per hour and summed over the city's
stations:

    water wheel     energy.waterwheel_rate      -- only where there is a river
    windmill        energy.windmill_rate {0..40} -- unstable, depends on weather
    coal station    energy.coal_plant_rate      -- at energy.coal_plant_fuel_draw
                                                   coal per hour

There is no weather in the world yet, so wind is rolled with a roll seeded by
node and hour: "unstable" is all the vault says about it. The coal station
**eats coal from the node** it stands in: no supply -- no generation, and an
energy blockade works by itself, without a single special mechanic.

**Battery self-discharge.** `energy.battery_selfdischarge` percent per day of
capacity. Credited not by tick but by elapsed time on the first access -- like
fallow on a plot: no point waking the world for a charge nobody looks at.

**Tariff.** `energy.tariff_default` -- TC per hundred energy. Money goes to
the city treasury: energy is released by the city, not by nature. The tariff
is edited by the charter from E3; until then the pool holds the vault default.

## What is not here yet

* **Consumers other than charging.** The automatic machine (D-035), deep
  mining (D-115) and body printing (D-024) will take theirs together with
  their mechanics; for now the pool is spent only on batteries;
* **Building meter** (D-135): `energy.home_draw_per_m2` is counted from
  building area, and there are no buildings before E3;
* **Geothermal and reactor**: they are beyond the alpha along with their planets.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import events, ledger, travel, world
from src.engine.errors import Refusal
from src.models.energy import EnergyPool
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node
from src.units import ENERGY_PER_TARIFF_UNIT, PERCENT, SECONDS_PER_HOUR, amount, amount_float, money

#: Thing classes from `build/recipes.json` (D-215). Behaviour binds to the
#: class, never to the item name: a second windmill or a peat-fired plant is
#: a data change. What each class generates is below, by vault rates.
WHEEL = "Водяное колесо"
WINDMILL = "Ветряк"
FUEL_PLANT = "Топливная станция"
BATTERY = "Аккумулятор"
#: Every generator class, for "the node has an energy source" checks.
GENERATOR_CLASSES = (WHEEL, WINDMILL, FUEL_PLANT)


class EnergyError(Refusal):
    pass


class NoGrid(EnergyError):
    """No pool outside a city: there one works from a battery, and it has to be brought."""


class NotEnough(EnergyError):
    """The pool does not have that much. An empty pool is a political event, not an error."""


class NotBattery(EnergyError):
    pass


async def grid_node(session: AsyncSession, node: Node) -> Node | None:
    """The delegate node of the city on whose territory this node stands.

    The city's built-up area is the planet delegate's children (D-045). The
    floodplain and the gully hang directly on the planet and do not belong to
    the city: there is no pool there.
    """
    if node.parent_id is None:
        return None
    parent = await session.get(Node, node.parent_id)
    if parent is None or parent.layer is not Layer.PLANET:
        return None
    return parent if node.layer is Layer.CITY else None


async def pool_of(
    session: AsyncSession, constants: Constants, node: Node, *, create: bool = True
) -> EnergyPool | None:
    """This node's city pool. Created on first need."""
    city = await grid_node(session, node)
    if city is None:
        return None
    found = (
        await session.execute(select(EnergyPool).where(EnergyPool.node_id == city.id))
    ).scalar_one_or_none()
    if found is not None or not create:
        return found

    pool = EnergyPool(
        node_id=city.id,
        stored=Decimal(0),
        tariff=Decimal(str(constants[R.ENERGY_TARIFF_DEFAULT])),
    )
    session.add(pool)
    await session.flush()
    return pool


async def produce(
    session: AsyncSession,
    constants: Constants,
    pool: EnergyPool,
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> float:
    """Bring the pool up to "now": generation of the city's stations over the elapsed time.

    Returns how much was added. Fuel is written off where the station stands:
    the city depends on whoever hauls coal (D-082).
    """
    moment = now or datetime.now(UTC)
    elapsed = (moment - pool.counted_at).total_seconds() / SECONDS_PER_HOUR
    if elapsed <= 0:
        return 0.0

    dice = rng or random.Random(f"{pool.node_id}:{int(moment.timestamp())}")
    nodes = (
        (await session.execute(select(Node).where(Node.parent_id == pool.node_id))).scalars().all()
    )

    added = 0.0
    for node in nodes:
        yard = await world.node_container(session, node)
        machines = (
            (await session.execute(select(Item).where(Item.container_id == yard.id)))
            .scalars()
            .all()
        )
        river = node.properties.get("вода") == "река"

        wheels = set(world.station_names(WHEEL))
        windmills = set(world.station_names(WINDMILL))
        fuel_plants = set(world.station_names(FUEL_PLANT))
        for machine in machines:
            if machine.type_key in wheels and river:
                added += constants[R.ENERGY_WATERWHEEL_RATE] * elapsed
            elif machine.type_key in windmills:
                wind = constants[R.ENERGY_WINDMILL_RATE]
                added += dice.uniform(wind.min, wind.max) * elapsed
            elif machine.type_key in fuel_plants:
                added += await _burn_fuel(session, constants, yard.id, elapsed)

    pool.stored = Decimal(str(float(pool.stored) + added))
    pool.counted_at = moment
    await session.flush()
    return added


async def _burn_fuel(
    session: AsyncSession, constants: Constants, container_id: uuid.UUID, hours: float
) -> float:
    """Burn fuel from the node and return the generation. No fuel -- the station stands.

    What counts as fuel is data (D-215): every material with an entry in
    `energy.fuel_energy` burns, each at its own energy per unit. The station
    eats `energy.coal_plant_fuel_draw` units per hour whatever the fuel --
    the draw is a property of the furnace, the yield of the material.
    """

    calories: dict[str, float] = constants[R.ENERGY_FUEL_ENERGY]
    need = constants[R.ENERGY_COAL_PLANT_FUEL_DRAW] * hours
    stacks = await world.locked_stacks(session, container_id, calories)
    have = sum(amount_float(stack.amount) for stack in stacks)
    to_burn = min(need, have)
    if to_burn <= 0:
        return 0.0

    #: Each fuel has its own calories, so the take is counted per stack.
    produced = 0.0
    left = amount(to_burn)
    for stack in stacks:
        if left <= 0:
            break
        take = min(left, stack.amount)
        produced += amount_float(take) * float(calories[stack.type_key])
        left -= take
    await world.consume(session, stacks, amount(to_burn))
    return produced


# --- fuel station (D-189) -----------------------------------------------------


async def plant_view(session: AsyncSession, constants: Constants, node: Node) -> dict | None:
    """What the station looks like from the outside: stock, draw, output.

    Supply is a matter of agreement between the city and the haulers, and both
    sides must see the same number -- hence the stock and the hours it lasts.
    """

    yard = await world.node_container(session, node)
    machines = (
        (await session.execute(select(Item).where(Item.container_id == yard.id))).scalars().all()
    )
    plant_names = set(world.station_names(FUEL_PLANT))
    plants = [thing for thing in machines if thing.type_key in plant_names]
    if not plants:
        return None

    fuels: dict[str, float] = constants[R.ENERGY_FUEL_ENERGY]
    stock = sum(amount_float(stack.amount) for stack in machines if stack.type_key in fuels)
    draw = constants[R.ENERGY_COAL_PLANT_FUEL_DRAW] * len(plants)
    return {
        "station": plants[0].type_key,
        "count": len(plants),
        #: What burns here -- every material with a fuel value (D-215).
        "fuel": ", ".join(sorted(fuels)),
        "fuels": sorted(fuels),
        "stock": round(stock, 1),
        #: Per hour: how much it eats and how much it gives while it eats.
        "draw": draw,
        "output": constants[R.ENERGY_COAL_PLANT_RATE] * len(plants),
        "hours_left": round(stock / draw, 1) if draw > 0 else None,
    }


async def fuel(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    item: Item,
    quantity: float | None = None,
) -> float:
    """Pour fuel from the hands into the station standing here (D-189).

    Anyone who came with coal may do it: hauling fuel is the supply mechanic
    itself, not a privilege of the authority. There is no way back -- pouring
    in is a handover, otherwise the city's fuel pile would be a common pocket.
    """

    if body.state is not BodyState.ALIVE:
        raise EnergyError("мёртвое тело ничего не грузит")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body without a node is a bug
        raise EnergyError("тело вне узла")
    view = await plant_view(session, constants, node)
    if view is None:
        raise EnergyError("здесь нет станции, которой нужно топливо")
    if item.type_key not in view["fuels"]:
        raise EnergyError(
            f"«{item.type_key}» не горит в «{view['station']}»: годится {view['fuel'].lower()}"
        )

    pocket = await world.body_container(session, body)
    if item.container_id != pocket.id:
        raise EnergyError("топливо грузят из рук")
    qty = amount_float(item.amount) if quantity is None else quantity
    if qty <= 0:
        raise EnergyError("грузить нечего")

    yard = await world.node_container(session, node)
    poured = await world.move_stack(session, item, yard, qty)
    await events.record(
        session,
        EventKind.ENERGY_FUELLED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        type_key=item.type_key,
        amount=poured,
    )
    return poured


# --- battery -----------------------------------------------------------------


def capacity(constants: Constants) -> float:
    return constants[R.ENERGY_BATTERY_CAPACITY]


def charge_of(constants: Constants, item: Item, *, now: datetime | None = None) -> float:
    """Battery charge with self-discharge -- by elapsed time.

    Energy is a perishable commodity: it cannot be stockpiled for years, and
    that makes it constant demand rather than treasure.
    """
    if item.charge is None:
        return 0.0
    moment = now or datetime.now(UTC)
    countdown = item.charged_at or item.created_at
    #: A day here is planetary, like all other terms of the world (D-008).
    hours_per_day = constants[R.TIME_DAY_TERRA]
    days = max(0.0, (moment - countdown).total_seconds() / SECONDS_PER_HOUR / hours_per_day)
    leaked = capacity(constants) * constants[R.ENERGY_BATTERY_SELFDISCHARGE] / PERCENT
    return max(0.0, float(item.charge) - leaked * days)


async def settle_charge(
    session: AsyncSession, constants: Constants, item: Item, *, now: datetime | None = None
) -> float:
    """Write into the battery its actual charge as of now."""
    moment = now or datetime.now(UTC)
    charge_ = charge_of(constants, item, now=moment)
    item.charge = Decimal(str(charge_))
    item.charged_at = moment
    await session.flush()
    return charge_


async def charge_battery(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    item: Item,
    amount_wanted: float | None = None,
    *,
    now: datetime | None = None,
) -> float:
    """Charge a battery from the city pool at the tariff.

    In person: charge is taken in the city and by hand. The taker pays -- into
    the city treasury: there is no free energy, and zero is a tariff too (D-085).

    Both the one in hand and the one standing here as a machine are charged
    (D-179): a battery is property of the place no less than a load.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise EnergyError("мёртвое тело не заряжает")
    await travel.require_here(session, body)

    if item.type_key not in world.station_names(BATTERY):
        raise NotBattery(f"{item.type_key!r} — не аккумулятор: энергия в мешке не лежит")

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise EnergyError("тело вне узла")
    pocket = await world.body_container(session, body)
    yard = await world.node_container(session, node)
    if item.container_id not in (pocket.id, yard.id):
        raise EnergyError("аккумулятор не в руках и не стоит здесь")
    pool = await pool_of(session, constants, node)
    if pool is None:
        raise NoGrid(
            "здесь нет городской сети: вне города работают от аккумулятора, и заряжают его в городе"
        )
    await produce(session, constants, pool, now=moment)

    have = await settle_charge(session, constants, item, now=moment)
    place = max(0.0, capacity(constants) - have)
    wants = place if amount_wanted is None else min(float(amount_wanted), place)
    will_give = min(wants, float(pool.stored))
    if will_give <= 0:
        raise NotEnough(
            f"в пуле {float(pool.stored):.0f} энергии, а в аккумуляторе места на {place:.0f}"
        )

    #: The tariff is given per hundred energy -- the bill is issued by it too.
    price = money(will_give / ENERGY_PER_TARIFF_UNIT * float(pool.tariff))
    if price > 0:
        account = await ledger.account_for(session, AccountKind.IDENTITY, body.identity_id)
        treasury = await ledger.account_for(session, AccountKind.CITY_TREASURY, pool.node_id)
        await ledger.transfer(
            session,
            PostingReason.ENERGY_BILL,
            debit=account.id,
            credit=treasury.id,
            amount=price,
            memo={"энергии": will_give, "тариф": float(pool.tariff)},
        )

    pool.stored = Decimal(str(float(pool.stored) - will_give))
    item.charge = Decimal(str(have + will_give))
    item.charged_at = moment
    await session.flush()

    await events.record(
        session,
        EventKind.ENERGY_CHARGED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(item.id),
        energy=will_give,
        paid=price,
        tariff=float(pool.tariff),
    )
    return will_give


async def draw_for_work(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    energy_needed: float,
    *,
    what: str,
    now: datetime | None = None,
) -> int:
    """Release energy from the pool for work and issue a bill at the tariff.

    Returns what was paid. Whoever burns pays (D-135): the machine's owner
    takes part in fuel costs like everyone, otherwise energy stops being an
    economy and becomes a subsidy.

    Written off **up front**, like batch materials: then no question arises of
    what to do with started work when the pool empties -- it is open in the
    vault (12-energy), and the engine may not decide it silently.
    """
    moment = now or datetime.now(UTC)
    if energy_needed <= 0:
        return 0

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise EnergyError("тело вне узла")
    pool = await pool_of(session, constants, node)
    if pool is None:
        raise NoGrid(
            f"{what} требует энергии, а городской сети здесь нет: вне города "
            "работают от аккумулятора"
        )
    await produce(session, constants, pool, now=moment)

    if float(pool.stored) < energy_needed:
        raise NotEnough(
            f"{what} требует {energy_needed:.0f} энергии, а в пуле "
            f"{float(pool.stored):.0f}: город без топлива стоит"
        )

    price = money(energy_needed / ENERGY_PER_TARIFF_UNIT * float(pool.tariff))
    if price > 0:
        account = await ledger.account_for(session, AccountKind.IDENTITY, body.identity_id)
        treasury = await ledger.account_for(session, AccountKind.CITY_TREASURY, pool.node_id)
        await ledger.transfer(
            session,
            PostingReason.ENERGY_BILL,
            debit=account.id,
            credit=treasury.id,
            amount=price,
            memo={"энергии": energy_needed, "за": what, "тариф": float(pool.tariff)},
        )

    pool.stored = Decimal(str(float(pool.stored) - energy_needed))
    await session.flush()
    await events.record(
        session,
        EventKind.ENERGY_DRAWN,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        energy=energy_needed,
        paid=price,
        work=what,
    )
    return price


async def price_of(
    session: AsyncSession, constants: Constants, node: Node, energy_needed: float
) -> int:
    """What this much energy costs here. For a forecast -- before spending."""
    pool = await pool_of(session, constants, node, create=False)
    tariff = float(pool.tariff) if pool is not None else constants[R.ENERGY_TARIFF_DEFAULT]
    return money(energy_needed / ENERGY_PER_TARIFF_UNIT * tariff)


async def tick_pools(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> float:
    """Bring all city pools up to "now". The world lives without players."""
    moment = now or datetime.now(UTC)
    pools = (await session.execute(select(EnergyPool))).scalars().all()
    result = 0.0
    for pool in pools:
        result += await produce(session, constants, pool, now=moment)
    return result


async def ensure_pools(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> int:
    """Create a pool for every city that has a built-up area.

    A city is a planet-layer node under which city-layer nodes stand. The pool
    is created once and lives by time from then on.
    """
    cities = (await session.execute(select(Node).where(Node.layer == Layer.CITY))).scalars().all()
    opened = 0
    for node in cities:
        # The second call creates the pool, and it is reached only by one that
        # has none: `and` does not evaluate the right side while the left is false.

        if (
            await pool_of(session, constants, node, create=False) is None
            and await pool_of(session, constants, node) is not None
        ):
            opened += 1
    return opened
