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

* **Consumers other than charging and heat.** The automatic machine (D-035),
  deep mining (D-115) and body printing (D-024) will take theirs together with
  their mechanics. Heat is already here (D-231): a heated node eats the pool
  round the clock, and `produce` takes it off in the same pass;
* **Building meter** (D-135): `energy.home_draw_per_m2` is counted from
  building area, and there are no buildings before E3;
* **Geothermal and reactor**: they are beyond the alpha along with their planets.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.db.base import remember
from src.engine import events, frost, ledger, stock, travel, world
from src.engine.errors import Refusal
from src.models.energy import EnergyPool
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node
from src.units import (
    ENERGY_PER_TARIFF_UNIT,
    HOURS_PER_DAY,
    SECONDS_PER_HOUR,
    amount,
    amount_float,
    money,
)

#: Thing classes from `build/recipes.json` (D-215). Behaviour binds to the
#: class, never to the item name: a second windmill or a peat-fired plant is
#: a data change. What each class generates is below, by vault rates.
WHEEL = "Водяное колесо"
WINDMILL = "Ветряк"
FUEL_PLANT = "Топливная станция"
#: The Forerunners' reactor (D-232): decay heat, no fuel and no people. It is
#: a relic -- found, never made -- and its energy never reaches a battery: it
#: pays for the relics of its own city and for nothing else. Free energy for
#: export does not exist.
REACTOR = "Реактор Предтеч"
#: When this reactor's countdown started, written into its node by the seed at
#: the moment Aurora's surface is laid (D-232). The Forerunners did not wait
#: for guests: a world that ran for a year before Aurora existed would
#: otherwise get the planet already dead.
REACTOR_SINCE = "реактор"
#: Every generator class, for "the node has an energy source" checks.
GENERATOR_CLASSES = (WHEEL, WINDMILL, FUEL_PLANT)


class EnergyError(Refusal):
    pass


class NoGrid(EnergyError):
    """No pool outside a city: there one works from a battery, and it has to be brought."""


class NotEnough(EnergyError):
    """The pool does not have that much. An empty pool is a political event, not an error."""


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
    session: AsyncSession,
    constants: Constants,
    node: Node,
    *,
    create: bool = True,
    lock: bool = False,
) -> EnergyPool | None:
    """This node's city pool. Created on first need.

    `lock` takes the row for the transaction. Everything that **moves** the
    stored energy asks for it: the pool is a remainder like money and grain
    (CLAUDE.md), and without the lock two charges read the same hundred and
    both spend it. Reads (`look`, the beacon, warmth) never lock and never
    create -- a glance at a place must not write to it.
    """
    city = await grid_node(session, node)
    if city is None:
        return None
    stmt = select(EnergyPool).where(EnergyPool.node_id == city.id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    found = (await session.execute(stmt)).scalar_one_or_none()
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
    """Bring the pool up to "now": what the city's stations made and what its heat ate.

    Returns the **net** change of the pool. Fuel is written off where the
    station stands: the city depends on whoever hauls coal (D-082). Heat is
    taken off in the same pass (D-231) -- generation and consumption share one
    stamp, and two passes over one city would sooner or later disagree about
    the hours.
    """
    moment = now or datetime.now(UTC)
    #: The row is taken for the transaction before anything is counted on it:
    #: the tick fills the pool while a player spends it, and both write the
    #: number they read (CLAUDE.md, review 2026-08-23).
    await session.execute(select(EnergyPool.id).where(EnergyPool.id == pool.id).with_for_update())
    await session.refresh(pool)
    elapsed = (moment - pool.counted_at).total_seconds() / SECONDS_PER_HOUR
    if elapsed <= 0:
        return 0.0

    dice = rng or random.Random(f"{pool.node_id}:{int(moment.timestamp())}")
    #: In node order: the frost step locks the same yards for its braziers in
    #: the same order, and two orders over one set of stacks are a deadlock
    #: waiting for a busy world.
    nodes = (
        (
            await session.execute(
                select(Node).where(Node.parent_id == pool.node_id).order_by(Node.id)
            )
        )
        .scalars()
        .all()
    )
    #: Heat is charged only where there is a climate (D-231): a heater standing
    #: in a Terran yard heats nothing, and a pool must not pay for it.
    city = await session.get(Node, pool.node_id)
    cold = city is not None and await frost.climate_of(session, city) is not None
    #: What the Forerunners' reactors of this city give over the same hours, and
    #: what their own relics burn. The reactor pays for those and stops there:
    #: it is never added to the pool, never reaches a battery, and never heats
    #: anything people built (D-232).
    relic = relic_heat = 0.0

    added = 0.0
    #: Heat is the pool's only round-the-clock consumer (D-231), and it is
    #: counted in the same pass as generation: two passes over one city with
    #: one stamp between them would sooner or later disagree about the hours.
    heat = 0.0
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
        standing: dict[str, float] = {}
        for machine in machines:
            #: By amount, not by row: two identical stoves nobody has touched
            #: fold into one stack (D-214), and a city must pay for both.
            standing[machine.type_key] = standing.get(machine.type_key, 0.0) + amount_float(
                machine.amount
            )
            if machine.type_key in wheels and river:
                added += constants[R.ENERGY_WATERWHEEL_RATE] * elapsed
            elif machine.type_key in windmills:
                wind = constants[R.ENERGY_WINDMILL_RATE]
                added += dice.uniform(wind.min, wind.max) * elapsed
            elif machine.type_key in fuel_plants:
                added += await _burn_fuel(session, constants, yard.id, elapsed)
        if cold:
            #: Two purses (D-232): what the Forerunners left is paid by their
            #: reactor, what people built is paid by the city.
            theirs, ours = frost.heat_draw(constants, standing)
            relic_heat += theirs * elapsed
            heat += ours * elapsed
        #: The reactor is read off the node it stands in, in the same pass as
        #: everything else here, and only if the thing itself is still there.
        reactors = sum(
            count for name, count in standing.items() if name in set(world.station_names(REACTOR))
        )
        if reactors:
            relic += reactor_output(constants, node, now=moment, count=reactors) * elapsed

    #: The pool never goes below nothing: a city that cannot pay for its heat
    #: does not owe the world, its nodes simply freeze -- and that is the whole
    #: price of a city on the permafrost.
    #: The relics' heat is paid by the relics' own energy first, and the city
    #: covers only what the reactor could not (D-232). The surplus is **not**
    #: kept: free energy for export does not exist, and a city living on a
    #: reactor still has nothing to charge a battery with -- nor a free stove
    #: for whatever its people carried in.
    before = float(pool.stored)
    unpaid = max(0.0, relic_heat - relic)
    pool.stored = Decimal(str(max(0.0, before + added - heat - unpaid)))
    pool.counted_at = moment
    await session.flush()
    #: The **net** change, not the generation: a tick that reported the output
    #: of a city whose heat ate all of it would be reporting a city that grew.
    return float(pool.stored) - before


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
    stacks = await stock.locked_stacks(session, container_id, calories)
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
    await stock.consume(session, stacks, amount(to_burn))
    return produced


# --- the Forerunners' reactor (D-232) -----------------------------------------


def reactor_output(constants: Constants, node: Node, *, now: datetime, count: float = 1.0) -> float:
    """What the reactor standing in this node still gives, energy per hour.

    A straight fading to nothing over `reactor.lifetime` days of real time. Not
    a switch: the city can see the day it will have to stand on its own coal
    long before that day comes.
    """
    stamp = (node.properties or {}).get(REACTOR_SINCE)
    if not stamp:
        return 0.0
    started = datetime.fromisoformat(str(stamp))
    if started.tzinfo is None:  # pragma: no cover -- the seed writes UTC
        started = started.replace(tzinfo=UTC)
    days = (now - started).total_seconds() / SECONDS_PER_HOUR / HOURS_PER_DAY
    left = 1 - days / constants[R.REACTOR_LIFETIME]
    #: Per reactor standing here: two in a stack give twice, exactly as two
    #: stoves eat twice (`frost.heat_draw`).
    return max(0.0, constants[R.REACTOR_OUTPUT] * left * count)


async def relic_power(
    session: AsyncSession, constants: Constants, node: Node, *, now: datetime | None = None
) -> float:
    """What the reactors of this node's city still give, energy per hour.

    Read rather than stored: a reactor is a thing standing in a node, and its
    age is written on that node. Remembered for the command -- every node of a
    city asks the same question, and warmth asks it about a whole ring of
    neighbours.
    """
    moment = now or datetime.now(UTC)
    city = await grid_node(session, node)
    if city is None:
        return 0.0

    async def read() -> float:
        holds = (
            await session.execute(
                select(Node, func.sum(Item.amount))
                .join(Container, Container.owner_id == Node.id)
                .join(Item, Item.container_id == Container.id)
                .where(
                    Node.parent_id == city.id,
                    Container.kind == ContainerKind.NODE,
                    Item.type_key.in_(world.station_names(REACTOR)),
                )
                .group_by(Node.id)
            )
        ).all()
        #: The thing **and** its node: the countdown is written on the node, the
        #: reactor is an item standing in it, and either missing means no output.
        #: `produce` asks the same pair, so the pool and the beacon never
        #: disagree about whether this city still has a reactor.
        return sum(
            reactor_output(constants, place, now=moment, count=amount_float(int(count)))
            for place, count in holds
        )

    return await remember(session, ("relic_power", city.id), read)


def reactor_dies_at(constants: Constants, node: Node) -> datetime | None:
    """When the reactor standing in this node goes silent. Empty -- no reactor here.

    Sent to the client instead of the output itself (D-225): the fading is a
    straight line, and a client holding `reactor.output` and `reactor.lifetime`
    from the catalog draws the rest of it without being told. What it cannot
    know is the day, because the anchor is a fact of this node alone.
    """
    stamp = (node.properties or {}).get(REACTOR_SINCE)
    if not stamp:
        return None
    started = datetime.fromisoformat(str(stamp))
    if started.tzinfo is None:  # pragma: no cover -- the seed writes UTC
        started = started.replace(tzinfo=UTC)
    return started + timedelta(days=constants[R.REACTOR_LIFETIME])


async def cities_with_power(session: AsyncSession, constants: Constants) -> set[uuid.UUID]:
    """Every city that has energy behind it, in two queries -- by city node id.

    For callers that ask about many places at once (the port list of a whole
    world, `ship.lit_ports`): asking `powered` per place would be two readings
    per place, and the number of places grows with every city a scout finds.
    """
    moment = datetime.now(UTC)
    alive = set(
        (await session.execute(select(EnergyPool.node_id).where(EnergyPool.stored > 0)))
        .scalars()
        .all()
    )
    reactors = (
        await session.execute(
            select(Node)
            .join(Container, Container.owner_id == Node.id)
            .join(Item, Item.container_id == Container.id)
            .where(
                Container.kind == ContainerKind.NODE,
                Item.type_key.in_(world.station_names(REACTOR)),
            )
            .distinct()
        )
    ).scalars()
    for place in reactors:
        if place.parent_id is not None and reactor_output(constants, place, now=moment) > 0:
            alive.add(place.parent_id)
    return alive


async def powered(
    session: AsyncSession, constants: Constants, node: Node, *, now: datetime | None = None
) -> bool:
    """Whether there is energy behind this node's machines at all.

    The city pool with anything in it, or a living reactor of the Forerunners.
    A read: no pool is created by asking.
    """
    pool = await pool_of(session, constants, node, create=False)
    if pool is not None and float(pool.stored) > 0:
        return True
    return await relic_power(session, constants, node, now=now) > 0


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
    fuel_key = item.type_key
    poured = await world.move_stack(session, item, yard, qty)
    await events.record(
        session,
        EventKind.ENERGY_FUELLED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        type_key=fuel_key,
        amount=poured,
    )
    #: Fuel the city ordered hauled pays per unit as it lands (D-248): the
    #: pour is the handover, the engine just watched it happen.
    from src.engine import works_city  # noqa: PLC0415 -- lazy: works_city imports energy

    await works_city.pay_fuel_delivery(session, constants, node, fuel_key, poured, body.identity_id)
    return poured


# --- battery -----------------------------------------------------------------
#: The cell itself lives in `engine.battery` (see its docstring for the seam).
#: Re-exported so `energy.charge_of` and `energy.BATTERY` read as they always
#: did, and so nothing outside had to move when the file was cut.
from src.engine.battery import (  # noqa: E402, F401
    BATTERY,
    BatteryError,
    NotBattery,
    batteries_in,
    capacity,
    charge_battery,
    charge_of,
    drain_batteries,
    settle_charge,
)


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
    pool = await pool_of(session, constants, node, lock=True)
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


def price_at(constants: Constants, pool: EnergyPool | None, energy_needed: float) -> int:
    """What this much energy costs at this pool. `None` -- there is no grid, and
    the default tariff answers for a place that has none.

    Split out of `price_of` for the caller that already holds the pool: the
    printer list walks every printer in the world and asks both what the pool
    holds and what it charges, and re-fetching the row for the second question
    is a query per city for nothing. Passing the pool **through** `price_of`
    would not have done it -- there `None` means "not given" and "no grid at
    all" at once, so a node without a grid would have been looked up twice.
    """
    tariff = float(pool.tariff) if pool is not None else constants[R.ENERGY_TARIFF_DEFAULT]
    return money(energy_needed / ENERGY_PER_TARIFF_UNIT * tariff)


async def price_of(
    session: AsyncSession, constants: Constants, node: Node, energy_needed: float
) -> int:
    """What this much energy costs here. For a forecast -- before spending."""
    return price_at(constants, await pool_of(session, constants, node, create=False), energy_needed)


async def tick_pools(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> float:
    """Bring all city pools up to "now". The world lives without players.

    Returns the net change over every pool: what was generated less what the
    heat of the cold planets ate (D-231).
    """
    moment = now or datetime.now(UTC)
    #: In city order: the frost step locks the same yards for its braziers, city
    #: by city, and two orders over one set of stacks are a deadlock waiting for
    #: a busy world.
    #: Only the ids here: `produce` takes each row for itself, one at a time,
    #: and holding every pool of the world locked for the whole pass would put
    #: the tick in the way of every player at once.
    pools = (await session.execute(select(EnergyPool).order_by(EnergyPool.node_id))).scalars().all()
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
