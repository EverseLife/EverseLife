# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: thrust, mass and what follows from them.

Split out of `engine/ship.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, ConstantError, Constants
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.db.base import remember
from src.engine import gear, stock, storage, world
from src.engine.ship import course, lines
from src.engine.ship._base import LIFE_SUPPORT, NotEnoughThrust
from src.engine.ship.belonging import nodes_of
from src.models.inventory import Container, ContainerKind, Item
from src.models.ship import Ship
from src.models.world import Layer, Node, Planet
from src.units import (
    AMOUNT_SCALE,
    HOURS_PER_DAY,
    KG_PER_TON,
    PERCENT,
    SECONDS_PER_HOUR,
    amount,
    amount_float,
)


async def _things(session: AsyncSession, ship: Ship) -> list[Item]:
    """Everything lying and standing aboard, storages included.

    A chest aboard is cargo together with its contents: mass that hides inside
    furniture is mass all the same, and forgetting it would make a chest a way
    to fly with a free hold.
    """
    nodes = await nodes_of(session, ship)
    if not nodes:  # pragma: no cover
        return []
    yards = (
        (
            await session.execute(
                select(Container).where(
                    Container.kind == ContainerKind.NODE,
                    Container.owner_id.in_([node.id for node in nodes]),
                )
            )
        )
        .scalars()
        .all()
    )
    if not yards:
        return []

    outer = list(
        (
            await session.execute(
                select(Item).where(Item.container_id.in_([yard.id for yard in yards]))
            )
        )
        .scalars()
        .all()
    )
    inner_ = (
        (
            await session.execute(
                select(Container).where(
                    Container.kind == ContainerKind.STORAGE,
                    Container.owner_id.in_([thing.id for thing in outer]),
                )
            )
        )
        .scalars()
        .all()
    )
    if not inner_:
        return outer
    inside = (
        (
            await session.execute(
                select(Item).where(Item.container_id.in_([box.id for box in inner_]))
            )
        )
        .scalars()
        .all()
    )
    return outer + list(inside)


async def mass(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    things: list[Item] | None = None,
) -> float:
    """The ship's mass, kg: the nodes plus everything aboard.

    Both terms are the player's decisions, and that is the point: a node added
    is both a place and extra mass, an engine added is both thrust and mass again.

    `things` is what lies aboard, when the caller has read it already: the
    summary asks seven questions of one hold and reads it once (D-230).
    """
    nodes = await nodes_of(session, ship)
    hull = len(nodes) * constants[R.SHIP_NODE_MASS]
    cargo = sum(
        gear.mass_of(catalog, thing.type_key, amount_float(thing.amount))
        for thing in await _aboard(session, ship, things)
    )
    return hull + cargo


async def _aboard(session: AsyncSession, ship: Ship, things: list[Item] | None) -> list[Item]:
    """What the caller read already, or a fresh reading of the hold."""
    return things if things is not None else await _things(session, ship)


async def thrust(
    session: AsyncSession, constants: Constants, ship: Ship, *, things: list[Item] | None = None
) -> float:
    """Total thrust of the engines standing aboard, kg.

    An engine is recognised by `ship.thrust` -- the vault's own table, not a
    list in the code: a new engine appears in the data and flies (D-090).
    """
    table = constants[R.SHIP_THRUST]
    return sum(
        float(table[thing.type_key]) * amount_float(thing.amount)
        for thing in await _aboard(session, ship, things)
        if thing.type_key in table
    )


async def engine_class(
    session: AsyncSession, constants: Constants, ship: Ship, *, things: list[Item] | None = None
) -> int | None:
    """The ship's class: **the weakest** engine aboard (D-037, D-054).

    The same weakest-link rule as the quality ceiling: one poor engine in the
    cluster holds the cluster back, and "we got there on three good ones and a
    bad one" does not happen. No engines -- no class at all.
    """
    table = constants[R.SHIP_ENGINE_CLASS]
    classes = [
        int(table[thing.type_key])
        for thing in await _aboard(session, ship, things)
        if thing.type_key in table
    ]
    return min(classes) if classes else None


async def life_support(
    session: AsyncSession, ship: Ship, *, things: list[Item] | None = None
) -> int:
    """How many life support systems stand aboard. None -- the hull does not cast off.

    Not a number of people any more (D-288): the crew has no ceiling, the
    draw is the ceiling the way mass is the hold's, and the system's one job
    is to breathe for whoever is aboard out of the vessels on its line
    (`oxygen`). What is counted is what **stands** (D-278).
    """
    return int(
        sum(
            amount_float(thing.amount)
            for thing in await _aboard(session, ship, things)
            if thing.type_key in world.station_names(LIFE_SUPPORT) and thing.installed
        )
    )


async def engines_aboard(
    session: AsyncSession, constants: Constants, ship: Ship, *, things: list[Item] | None = None
) -> list[Item]:
    """The engines standing aboard, in id order: what the fuel lines hang on (D-288)."""
    table = constants[R.SHIP_THRUST]
    return sorted(
        (
            thing
            for thing in await _aboard(session, ship, things)
            if thing.type_key in table and thing.installed
        ),
        key=lambda thing: thing.id,
    )


async def fuel_stacks(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    things: list[Item] | None = None,
) -> list[Item]:
    """The fuel the engines can reach: what lies in the vessels on their lines (D-288).

    The vessels the owner named when a line was drawn, and no other (as
    amended 2026-09-04: a port without a line draws from nothing) -- a tank, a
    canister, a cylinder alike. What lies in the hold uninstalled is cargo: it
    weighs and does not burn (D-230). No engine, no line, no fuel to reach.
    """
    engines = await engines_aboard(session, constants, ship, things=things)
    return await lines.stacks_for(session, catalog, ship, engines, lines.fuel_port(), things=things)


async def fuel_aboard(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    things: list[Item] | None = None,
) -> float:
    return sum(
        amount_float(thing.amount)
        for thing in await fuel_stacks(session, constants, catalog, ship, things=things)
    )


def fuel_energy(constants: Constants, type_key: str) -> float:
    """Reference units one unit of this fuel is worth (D-252).

    The spend is computed in rocket-fuel units (`fuel_for`); the tanks pay by
    density: a unit of kerosene fuel closes 1.25 reference units, so the same
    tank flies further. A kind the table does not name is worth one -- the
    reference itself, and whatever arrives before its line does.
    """
    return float(constants[R.SHIP_FUEL_ENERGY].get(type_key, 1.0))


async def fuel_worth(
    session: AsyncSession, constants: Constants, catalog: Catalog, ship: Ship
) -> float:
    """What the lines hold, in reference units (D-252).

    `fuel_aboard` keeps counting physical units -- that is what the console
    shows and what has mass; this is what a passage can pay with.
    """
    return sum(
        amount_float(thing.amount) * fuel_energy(constants, thing.type_key)
        for thing in await fuel_stacks(session, constants, catalog, ship)
    )


async def spend_fuel(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    need: float,
    *,
    stacks: list[Item] | None = None,
) -> float:
    """Burn `need` reference units out of the lines. Returns the units burnt.

    Stack by stack in **line** order (D-288), each paying at its own worth
    (D-252): a tank holding kerosene beside rocket fuel spends fewer physical
    units for the same passage. `stacks` are already-locked rows from
    `burn_checked`, so the check and the burn share one lock; without them
    the vessels are locked here, and running dry is the caller's arithmetic
    being wrong.
    """
    if stacks is None:
        stacks = await stock.lock_items(
            session, await fuel_stacks(session, constants, catalog, ship), ordered=True
        )
    burnt = 0.0
    left = need
    for stack in stacks:
        if left <= _FUEL_EPS:
            break
        worth = fuel_energy(constants, stack.type_key)
        asked = min(amount_float(stack.amount), left / worth)
        got = amount_float(await stock.consume(session, [stack], amount(asked)))
        burnt += got
        left -= got * worth
    return burnt


#: Spend splits into thousandths, like every amount: the last digit of a
#: representation must not demand one more stack.
_FUEL_EPS = 1 / AMOUNT_SCALE


async def burn_checked(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    need: float,
    whole: float,
) -> tuple[float, float]:
    """Lock the lines' vessels, weigh them, and burn only if `whole` is covered.

    Returns (burnt, worth aboard). Burnt is zero when the worth falls short
    -- the caller words the refusal; the arithmetic stays under one lock, so
    a canister filling from the same tank between the check and the burn
    cannot let a leg fly on fuel it never paid (the quality bar: amounts
    change only under the row lock).
    """
    stacks = await stock.lock_items(
        session, await fuel_stacks(session, constants, catalog, ship), ordered=True
    )
    worth = sum(
        amount_float(stack.amount) * fuel_energy(constants, stack.type_key) for stack in stacks
    )
    if worth + _FUEL_EPS < whole:
        return 0.0, worth
    return await spend_fuel(session, constants, catalog, ship, need, stacks=stacks), worth


async def engines(
    session: AsyncSession, constants: Constants, ship: Ship, *, things: list[Item] | None = None
) -> list[dict[str, object]]:
    """What drives the ship, engine by engine: name, count, thrust each, class.

    For the console (D-230): the owner reads which engines stand aboard and
    what each gives, not a single sum they cannot act on.
    """
    thrusts = constants[R.SHIP_THRUST]
    classes = constants[R.SHIP_ENGINE_CLASS]
    counts: dict[str, float] = {}
    for thing in await _aboard(session, ship, things):
        if thing.type_key in thrusts:
            counts[thing.type_key] = counts.get(thing.type_key, 0.0) + amount_float(thing.amount)
    return [
        {
            "name": name,
            "count": count,
            "thrust": float(thrusts[name]),
            "class": int(classes.get(name, 1)),
        }
        for name, count in sorted(counts.items())
    ]


async def mass_parts(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    things: list[Item] | None = None,
) -> dict[str, float]:
    """The mass by where it comes from: the hull, the machines, the cargo.

    Three numbers the owner can act on separately (D-230): a node is cut by
    not laying it, a machine by taking it down, cargo by unloading -- and a
    single total says nothing about which of the three is the heavy one.
    """
    nodes = await nodes_of(session, ship)
    machines = 0.0
    cargo = 0.0
    for thing in await _aboard(session, ship, things):
        weight = gear.mass_of(catalog, thing.type_key, amount_float(thing.amount))
        if _placeable(catalog, thing.type_key):
            machines += weight
        else:
            cargo += weight
    return {
        "hull": len(nodes) * constants[R.SHIP_NODE_MASS],
        "machines": machines,
        "cargo": cargo,
    }


def _placeable(catalog: Catalog, type_key: str) -> bool:
    """A machine, furniture or a vessel: what stands in a room rather than lies
    in it (D-278, D-288). The same test as `station.placeable`, asked of the
    catalog directly so that physics does not pull the craft package in
    behind the station module."""
    try:
        kind = catalog.recipes.recipe(type_key).kind
    except ConstantError:  # raw material has no recipe, and that is cargo
        return False
    return kind in (ItemKind.STATION, ItemKind.FURNITURE) or storage.is_vessel(catalog, type_key)


async def ratio(session: AsyncSession, constants: Constants, catalog: Catalog, ship: Ship) -> float:
    """Thrust-to-mass. Everything about a passage follows from this one number."""
    weight = await mass(session, constants, catalog, ship)
    if weight <= 0:  # pragma: no cover -- a ship always has at least one node
        return 0.0
    return await thrust(session, constants, ship) / weight


async def _sphere(session: AsyncSession, planet: Planet) -> Node | None:
    """The planet's own node: the one carrying its orbit.

    Ship delegates live on the space layer too, and they also carry a planet --
    they are told apart by hanging on one. A planet hangs on nothing.
    """
    return (
        (
            await session.execute(
                select(Node).where(
                    Node.layer == Layer.SPACE,
                    Node.planet == planet,
                    Node.parent_id.is_(None),
                )
            )
        )
        .scalars()
        .first()
    )


async def orbits_of(session: AsyncSession) -> dict[Planet, course.Orbit]:
    """Every planet that goes round the star, with its orbit. One reading per command.

    Deferred worlds are here too (D-104): a planet one may not land on still
    pulls, and a hull may be bent round it. Where a passage may **end** is the
    beacons' business (`lit_ports`), not the sky's.
    """

    async def read() -> dict[Planet, course.Orbit]:
        spheres = (
            (
                await session.execute(
                    select(Node).where(Node.layer == Layer.SPACE, Node.parent_id.is_(None))
                )
            )
            .scalars()
            .all()
        )
        found: dict[Planet, course.Orbit] = {}
        for sphere in spheres:
            circle = world.orbit_of(sphere)
            if circle is None or sphere.planet is None:
                continue
            found[sphere.planet] = (
                float(circle["radius"]),
                float(circle["period_days"]),
                float(circle["phase"]),
            )
        return found

    return await remember(session, ("orbits",), read)


async def deferred_planets(session: AsyncSession) -> frozenset[Planet]:
    """The worlds drawn but not playable (D-104): no corridor ends at them,
    though a passage may still bend round them."""

    async def read() -> frozenset[Planet]:
        spheres = (
            (
                await session.execute(
                    select(Node).where(Node.layer == Layer.SPACE, Node.parent_id.is_(None))
                )
            )
            .scalars()
            .all()
        )
        return frozenset(
            sphere.planet
            for sphere in spheres
            if sphere.planet is not None and (sphere.properties or {}).get(world.DEFERRED)
        )

    return await remember(session, ("deferred_planets",), read)


async def sky_days(session: AsyncSession, at: datetime) -> float:
    """Days since the world's epoch: the argument every orbit is read at."""
    origin = await world.epoch(session)
    gone = 0.0 if origin is None else (at - origin).total_seconds()
    return gone / SECONDS_PER_HOUR / HOURS_PER_DAY


async def passage_curve(
    session: AsyncSession,
    constants: Constants,
    here: Planet,
    there: Planet,
    *,
    at: datetime,
) -> tuple[course.Sample, ...]:
    """Delta-v against flight time for a passage cast off at `at` (D-271).

    **Not a constant of the route but of the moment.** Planets go round the
    star at their own periods, and the arc between any two of them is priced
    by where both stand: a ship setting out at the wrong hour pays many times
    over on the fast end, or waits weeks on the cheap one -- which is why
    interplanetary trade goes in waves and a passage is planned rather than
    simply started.

    Empty means the sky has no such passage at all -- a planet without an
    orbit, or the same planet twice: there is no corridor from a planet to
    itself (D-245).

    Off the event loop: a cold curve is a few hundred Lambert solutions, and
    every socket would wait for them.
    """
    if here is there:
        return ()
    orbits = await orbits_of(session)
    if here not in orbits or there not in orbits:
        return ()
    days = await sky_days(session, at)
    return await asyncio.to_thread(course.curve, constants, orbits[here], orbits[there], days)


async def corridors(
    session: AsyncSession, constants: Constants, *, at: datetime
) -> list[dict[str, object]]:
    """Every interplanetary corridor with its calendar: the cheapest passage
    for each of the coming days, by pair of planets.

    For the map, which draws the corridors and says when the window opens. The
    engine forecasts and the client leafs through: the arc is not arithmetic a
    client may be asked to repeat (D-271), and a forecast for sixty days is a
    picture, not a quote -- the flight is settled at the casting off.
    """
    orbits = await orbits_of(session)
    shut = await deferred_planets(session)
    days = await sky_days(session, at)
    lines: list[dict[str, object]] = []
    #: Only between worlds one may go to: a deferred planet is drawn and
    #: bent round, not flown to (D-104), and a corridor to it would promise
    #: a passage the engine refuses.
    named = sorted(
        (pair for pair in orbits.items() if pair[0] not in shut), key=lambda pair: pair[0].value
    )
    horizon = int(constants[R.ORBIT_CALENDAR_DAYS])
    for i, (one, first) in enumerate(named):
        for other, second in named[i + 1 :]:
            forecast = await asyncio.to_thread(
                course.calendar, constants, first, second, days, horizon
            )
            lines.append(
                {
                    "a": one.value,
                    "b": other.value,
                    "days": [
                        {"day": day.day, "dv": day.dv, "hours": day.hours} for day in forecast
                    ],
                }
            )
    return lines


def gravity(constants: Constants, planet: Planet) -> float:
    """How heavy this world is, as a share of Terra's (D-245).

    The first number by which planets differ from one another at all, before
    any geology: a heavy world is dear to leave and dear to come down onto. A
    planet the vault says nothing about weighs what Terra weighs -- a missing
    line must not make a world free to leave.
    """
    table = constants[R.PLANET_GRAVITY]
    return float(table.get(planet.value, 1.0))


def climb_hours(constants: Constants, planet: Planet, thrust_ratio: float) -> float:
    """The climb from a spaceport of this planet to its orbit (D-245).

    The planet's gravity times the vault's base, stretched by thrust-to-mass
    exactly as a passage between worlds is: a heavy hull crawls off a heavy
    planet, and that is the same sentence said of the same numbers.
    """
    return passage_hours(
        constants, constants[R.SHIP_ASCENT_HOURS] * gravity(constants, planet), thrust_ratio
    )


def fall_hours(constants: Constants, planet: Planet, thrust_ratio: float) -> float:
    """The descent from orbit onto a spaceport. Shorter than the climb: coming
    down, the gravity one climbed against is on the ship's side."""
    return passage_hours(
        constants, constants[R.SHIP_DESCENT_HOURS] * gravity(constants, planet), thrust_ratio
    )


def passage_hours(constants: Constants, table_hours: float, thrust_ratio: float) -> float:
    """How long the passage takes for this thrust-to-mass.

    The floor is `ship.route_min_share` of the table: speed has a ceiling,
    otherwise it is enough to hang engines on a single node.
    """
    if thrust_ratio <= 0:  # pragma: no cover -- checked before the call
        raise NotEnoughThrust(key="ship-no-thrust-at-all")
    stretched = table_hours * constants[R.SHIP_REFERENCE_RATIO] / thrust_ratio
    floor = table_hours * constants[R.SHIP_ROUTE_MIN_SHARE] / PERCENT
    return max(floor, stretched)


def efficiency(constants: Constants, klass: int | None) -> float:
    """How much of the baseline burn this class of ship spends (D-235).

    Class is power and **efficiency**, never a licence for a route: a
    first-class engine reaches Pyroxis like any other, it just takes longer to
    get there and burns more doing it. The table is keyed by engine name, and
    the ship's class is the weakest engine aboard (`engine_class`).
    """
    if klass is None:
        return 1.0
    table = constants[R.SHIP_ENGINE_EFFICIENCY]
    thrusts = constants[R.SHIP_THRUST]
    classes = constants[R.SHIP_ENGINE_CLASS]
    for name in thrusts:
        if int(classes.get(name, 1)) == klass and name in table:
            return float(table[name])
    return 1.0  # pragma: no cover -- the vault gives a line per engine


def fuel_for(
    constants: Constants, weight: float, hours: float, *, klass: int | None = None
) -> float:
    """Fuel for the passage: by mass, by days under way, and by the class of
    what is pushing.

    So an extra node costs money on every passage rather than once at building:
    the price of a badly designed ship is paid all its life. And a better
    engine is worth building for the fuel alone (D-235) -- there is no route it
    unlocks, only routes it makes cheaper.
    """
    spend = constants[R.SHIP_FUEL_PER_TON_DAY] * weight / KG_PER_TON * hours / HOURS_PER_DAY
    return spend * efficiency(constants, klass)
