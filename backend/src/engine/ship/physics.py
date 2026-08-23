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

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import gear, world
from src.engine.ship._base import FUEL, LIFE_SUPPORT, NotEnoughThrust
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


async def mass(session: AsyncSession, constants: Constants, catalog: Catalog, ship: Ship) -> float:
    """The ship's mass, kg: the nodes plus everything aboard.

    Both terms are the player's decisions, and that is the point: a node added
    is both a place and extra mass, an engine added is both thrust and mass again.
    """
    nodes = await nodes_of(session, ship)
    hull = len(nodes) * constants[R.SHIP_NODE_MASS]
    cargo = sum(
        gear.mass_of(catalog, thing.type_key, amount_float(thing.amount))
        for thing in await _things(session, ship)
    )
    return hull + cargo


async def thrust(session: AsyncSession, constants: Constants, ship: Ship) -> float:
    """Total thrust of the engines standing aboard, kg.

    An engine is recognised by `ship.thrust` -- the vault's own table, not a
    list in the code: a new engine appears in the data and flies (D-090).
    """
    table = constants[R.SHIP_THRUST]
    return sum(
        float(table[thing.type_key]) * amount_float(thing.amount)
        for thing in await _things(session, ship)
        if thing.type_key in table
    )


async def engine_class(session: AsyncSession, constants: Constants, ship: Ship) -> int | None:
    """The ship's class: **the weakest** engine aboard (D-037, D-054).

    The same weakest-link rule as the quality ceiling: one poor engine in the
    cluster holds the cluster back, and "we got there on three good ones and a
    bad one" does not happen. No engines -- no class at all.
    """
    table = constants[R.SHIP_ENGINE_CLASS]
    classes = [
        int(table[thing.type_key])
        for thing in await _things(session, ship)
        if thing.type_key in table
    ]
    return min(classes) if classes else None


async def life_support(session: AsyncSession, constants: Constants, ship: Ship) -> int:
    """How many people the ship holds: `ship.life_support_crew` per system."""
    systems = sum(
        amount_float(thing.amount)
        for thing in await _things(session, ship)
        if thing.type_key in world.station_names(LIFE_SUPPORT)
    )
    return int(systems * constants[R.SHIP_LIFE_SUPPORT_CREW])


async def fuel_aboard(session: AsyncSession, ship: Ship) -> float:
    return sum(
        amount_float(thing.amount)
        for thing in await _things(session, ship)
        if thing.type_key in world.station_names(FUEL)
    )


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
    classes = constants[R.SHIP_ROUTE_CLASS]
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
                "class": int(classes.get(key, 1)),
            }
        )
    return lines


def route_class(constants: Constants, here: Planet, there: Planet) -> int:
    """The lowest engine class the route takes. Within a planet -- any."""
    if here is there:
        return 1
    classes = constants[R.SHIP_ROUTE_CLASS]
    key = route_key(here, there)
    return int(classes[key]) if key in classes else 1


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


def fuel_for(constants: Constants, weight: float, hours: float) -> float:
    """Fuel for the passage: by mass and by days under way.

    So an extra node costs money on every passage rather than once at building:
    the price of a badly designed ship is paid all its life.
    """
    return constants[R.SHIP_FUEL_PER_TON_DAY] * weight / KG_PER_TON * hours / HOURS_PER_DAY
