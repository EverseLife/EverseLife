# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: thrust, mass and what follows from them.

Split out of `engine/ship.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import math
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, ConstantError, Constants
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.engine import gear, world
from src.engine.ship._base import FUEL, LIFE_SUPPORT, TANK, NotEnoughThrust
from src.engine.ship.belonging import nodes_of
from src.models.inventory import Container, ContainerKind, Item
from src.models.ship import Ship
from src.models.world import Layer, Node, Planet
from src.units import (
    HOURS_PER_DAY,
    KG_PER_TON,
    PERCENT,
    SECONDS_PER_HOUR,
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
    session: AsyncSession, constants: Constants, ship: Ship, *, things: list[Item] | None = None
) -> int:
    """How many people the ship holds: `ship.life_support_crew` per system."""
    systems = sum(
        amount_float(thing.amount)
        for thing in await _aboard(session, ship, things)
        if thing.type_key in world.station_names(LIFE_SUPPORT)
    )
    return int(systems * constants[R.SHIP_LIFE_SUPPORT_CREW])


async def fuel_stacks(session: AsyncSession, ship: Ship) -> list[Item]:
    """The fuel the engines can reach: what lies in the **tanks** aboard (D-230).

    A canister of fuel in the hold is cargo -- it weighs, it does not burn.
    The tanks are machines standing in the rooms, and their insides are the
    reserve; fuel lying anywhere else aboard is not counted.
    """
    nodes = await nodes_of(session, ship)
    if not nodes:  # pragma: no cover
        return []
    yards = select(Container.id).where(
        Container.kind == ContainerKind.NODE, Container.owner_id.in_([node.id for node in nodes])
    )
    tanks = select(Item.id).where(
        Item.container_id.in_(yards), Item.type_key.in_(world.station_names(TANK))
    )
    insides = select(Container.id).where(
        Container.kind == ContainerKind.STORAGE, Container.owner_id.in_(tanks)
    )
    rows = await session.execute(
        select(Item)
        .where(Item.container_id.in_(insides), Item.type_key.in_(world.station_names(FUEL)))
        .order_by(Item.id)
    )
    return list(rows.scalars().all())


async def fuel_aboard(session: AsyncSession, ship: Ship) -> float:
    return sum(amount_float(thing.amount) for thing in await fuel_stacks(session, ship))


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
    """A machine or furniture: what stands in a room rather than lies in it.
    The same test as `station.placeable`, asked of the catalog directly so
    that physics does not pull the craft package in behind the station module."""
    try:
        kind = catalog.recipes.recipe(type_key).kind
    except ConstantError:  # raw material has no recipe, and that is cargo
        return False
    return kind in (ItemKind.STATION, ItemKind.FURNITURE)


async def ratio(session: AsyncSession, constants: Constants, catalog: Catalog, ship: Ship) -> float:
    """Thrust-to-mass. Everything about a passage follows from this one number."""
    weight = await mass(session, constants, catalog, ship)
    if weight <= 0:  # pragma: no cover -- a ship always has at least one node
        return 0.0
    return await thrust(session, constants, ship) / weight


def route_key(one: Planet, other: Planet) -> str:
    """The route key: the pair of planets in alphabetical order.

    A route is undirected, like an edge of the map, so one key describes both
    directions -- two would sooner or later disagree with each other.
    """
    return "-".join(sorted((one.value, other.value)))


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


async def separation(
    session: AsyncSession, here: Planet, there: Planet, at: datetime
) -> tuple[float, float, float] | None:
    """How far two planets stand apart now, and the two ends that distance lives between.

    Returns `(now, together, opposite)`. Together is `|Ra - Rb|` -- the planets
    on one side of the star, the shortest the corridor between them ever is;
    opposite is `Ra + Rb`, the longest. None means the world has no orbits to
    ask (an old world, a test): the caller then has nothing to modulate by.
    """
    one = await _sphere(session, here)
    other = await _sphere(session, there)
    if one is None or other is None:
        return None
    first = world.orbit_of(one)
    second = world.orbit_of(other)
    if first is None or second is None:
        return None

    origin = await world.epoch(session)
    gone = 0.0 if origin is None else (at - origin).total_seconds()
    days = gone / SECONDS_PER_HOUR / HOURS_PER_DAY

    def place(orbit: dict[str, float]) -> tuple[float, float]:
        angle = orbit["phase"] + math.tau * days / orbit["period_days"]
        return orbit["radius"] * math.cos(angle), orbit["radius"] * math.sin(angle)

    ax, ay = place(first)
    bx, by = place(second)
    radii = (first["radius"], second["radius"])
    return math.dist((ax, ay), (bx, by)), abs(radii[0] - radii[1]), sum(radii)


async def base_hours(
    session: AsyncSession,
    constants: Constants,
    here: Planet,
    there: Planet,
    *,
    at: datetime,
) -> float | None:
    """The passage's table time at the reference thrust-to-mass, hours.

    **Not a constant of the route but of the moment.** Planets go round the
    star at their own periods, so the way between any two of them stretches and
    shrinks by itself: the vault gives the two ends -- in conjunction and in
    opposition -- and the sky decides where between them today falls. A ship
    setting out at the wrong hour pays four to five times over, which is why
    interplanetary trade goes in waves and a passage is planned rather than
    simply started.

    None means there is no such route in the vault at all -- and that is a
    refusal, not a zero: the engine invents no ways between planets.
    """
    if here is there:
        return constants[R.SHIP_HOP_HOURS]
    key = route_key(here, there)
    window = constants[R.SHIP_ROUTE_WINDOW_HOURS]
    apart = constants[R.SHIP_ROUTE_APART_HOURS]
    if key not in window or key not in apart:
        return None

    near, far = float(window[key]), float(apart[key])
    spread = await separation(session, here, there, at)
    if spread is None:
        #: A world with no orbits to ask -- and the answer is the **long** end.
        #: Not knowing the sky must never come out cheaper than knowing it:
        #: were the planets ever to go missing from under this query, flights
        #: would silently turn into bargains and nobody would notice.
        return far
    now, together, opposite = spread
    if opposite <= together:  # pragma: no cover -- two planets on one orbit
        return near
    share = min(1, max(0, (now - together) / (opposite - together)))
    return near + (far - near) * share


def corridors(constants: Constants) -> list[dict[str, object]]:
    """Every interplanetary route the vault knows: its two ends and its class.

    For the map, which draws the corridors and what a passage along them costs
    right now. The ends travel rather than the answer on purpose: the client
    already has the orbits and winds them forward, so it can price a passage
    for any moment -- and a player planning a run needs exactly that, not
    today's number alone. The engine stays the authority: this is a forecast,
    and the flight is settled by `base_hours` at the moment of casting off.
    """
    window = constants[R.SHIP_ROUTE_WINDOW_HOURS]
    apart = constants[R.SHIP_ROUTE_APART_HOURS]
    lines: list[dict[str, object]] = []
    for key in sorted(window):
        if key not in apart:  # pragma: no cover -- the vault gives both ends
            continue
        first, _, second = key.partition("-")
        lines.append(
            {
                "a": first,
                "b": second,
                "window_hours": float(window[key]),
                "apart_hours": float(apart[key]),
            }
        )
    return lines


def passage_hours(constants: Constants, table_hours: float, thrust_ratio: float) -> float:
    """How long the passage takes for this thrust-to-mass.

    The floor is `ship.route_min_share` of the table: speed has a ceiling,
    otherwise it is enough to hang engines on a single node.
    """
    if thrust_ratio <= 0:  # pragma: no cover -- checked before the call
        raise NotEnoughThrust("тяги нет вовсе")
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
