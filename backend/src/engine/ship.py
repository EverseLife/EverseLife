# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The ship: a group of nodes coupled to a spaceport by one edge (D-201, D-202).

A ship is **not a thing standing in a node** and not a layer of its own. Its
rooms are ordinary nodes of the same graph: with their own area, their own
chat, their own machines and their own edges. Outwards the group faces through
exactly one **connector** -- the node laid first -- and docking is one edge
between that connector and the spaceport.

Hence the whole of space is two operations on one edge:

    docking    = travel.connect(port, connector)
    undocking  = travel.disconnect(port, connector)

and the flight is the state of having no such edge. A body aboard needs no
"in flight" flag: there is simply nowhere to step off to.

## Why it is a subgraph and not a vehicle

A vehicle is harnessed to and carries cargo in a hold (`engine.transport`). One
does not walk inside a vehicle -- while inside a ship people must walk: to the
bridge, to the hold, to the engine room. Two models of one object would have
diverged the day somebody asked where a person flying to Aurora actually is.
As a subgraph the answer is the same as everywhere else: in a node.

## The ship grows by a node at a time

One comes to a spaceport and lays a foundation, giving up an **Основа узла
корабля**. The first node appears -- the base, the connector and the docking
point at once. The same action from any node aboard lays one more, joined to
the one it was laid from. A ship is therefore built the way a city is settled,
and its shape is somebody's decision rather than a recipe's.

A node aboard is a **building** from the first second: machines take area
(D-106), so a hull section has `ship.node_area` of it. What the ship can do is
set by what stands in it -- engines, navigation, life support are machines, not
lines of a recipe.

## Speed is thrust against mass

    ratio = sum of thrust of the engines / (mass of the nodes + everything aboard)
    hours = table time * ship.reference_ratio / ratio

Below `ship.min_thrust_ratio` the ship **does not undock at all** -- it does not
"fly slowly", it does not tear off, and that is known before the attempt rather
than after. Faster than `ship.route_min_share` of the table it does not go: a
speed ceiling, otherwise it is enough to hang engines on a single node.

There is no capacity number anywhere. Overload shows itself as a longer passage
and, in the limit, as a ship that stays in port -- which reads better than
"capacity exceeded". What a crew member carries in their own hands is not
weighed: a pocket against a hull is rounding.

## Two things an undocked ship must never become

A ship with no edges cannot be reached: fuel cannot be brought to it and nobody
aboard can walk off. So casting off is refused without fuel for at least the
way back into the very port being left -- the cheapest passage there is. A trap
with no way out is not built in this world (pillar P6), and this is the only
one a ship could have created.

The other one is a second way in. The connector must stay alone, so
exploration from aboard is refused as well (`engine.explore`): a find arrives
with an edge from the node it was made from, and an edge out of a hull would
quietly weld the ship to a wild node past the inspection at the gangway.

## What the engine keeps no list of

Neither engines nor routes. Thrust and class come by the item's name from
`ship.thrust` and `ship.engine_class`, passage times from
`ship.route_window_hours` and `ship.route_apart_hours` keyed by the pair of
planets -- exactly as a vehicle's capacity comes by its name (D-090). A
second-class engine appears in the vault and flies without a release.

## A passage costs what the sky costs today

The two vault numbers are the **ends** of a route, not its price: planets go
round the star at their own periods, so the way between any two of them
stretches and shrinks by itself. In conjunction Terra and Aurora are ten hours
apart, in opposition two days -- and everything between is the sky's doing, not
a setting. Hence the rule the whole of space trade rests on: **a passage is
planned.** Windows come round every two to five weeks of real time, and setting
out at the wrong hour costs four to five times over, in hours and in fuel
alike.

The time is settled once, at casting off, and never recomputed: a sky turning
under a ship already under way would make the passage longer than the one paid
for.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, gear, travel, world
from src.engine.errors import Refusal
from src.engine.jobs import enqueue, handler
from src.models.estate import Building
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.job import Job, JobKind, JobState
from src.models.ship import Ship
from src.models.world import Edge, Layer, Node, Planet, Surface
from src.units import (
    AMOUNT_SCALE,
    HOURS_PER_DAY,
    KG_PER_TON,
    PERCENT,
    ROUND_HOURS,
    ROUND_MASS,
    ROUND_RATIO,
    SECONDS_PER_HOUR,
    amount_float,
)
from src.units import amount as to_amount

#: Thing classes from the vault (D-202, D-215): behaviour binds to a class,
#: never to an item name -- a second kind of foundation or fuel is data.
#: The class a node aboard is laid from.
FOUNDATION = "Основа корабля"
#: The class of machines a ship couples to and is laid down at.
SPACEPORT = "Верфь"
#: The class of machines that decide how many people the ship holds.
LIFE_SUPPORT = "Жизнеобеспечение"
#: The class of what a passage burns.
FUEL = "Корабельное топливо"

#: The node property marking a node as being aboard. A property rather than a
#: fifth planet: the list of planets drags its own day length and environment
#: wear behind it, a property drags nothing (D-201).
ABOARD = "борт"

#: Amounts split into thousandths, so "was there enough" must tolerate the last
#: digit -- otherwise exactly enough fuel turns out to be short.
_EPS = 1 / AMOUNT_SCALE


def _gangway_seconds(constants: Constants, berth: int) -> float:
    """How long the gangway takes to walk: `ship.berth_seconds` per berth.

    A yard's berths are numbered, and the walk to one is as long as its number:
    the ship at the first berth is a second from the yard, the one at the fifth
    is five. So a busy port is a slower port, and that is the whole cost of
    somebody else being there before you.
    """
    return berth * constants[R.SHIP_BERTH_SECONDS]


async def _free_berth(session: AsyncSession, port: Node) -> int:
    """The lowest berth free at this port.

    **Lowest**, not next: casting off leaves a hole, and the next arrival fills
    it rather than walking past it to the end of the pier. A port that has seen
    a hundred ships come and go still boards the next one in a second.
    """
    taken = set(
        (
            await session.execute(
                select(Ship.berth).where(
                    Ship.docked_node_id == port.id, Ship.berth.is_not(None)
                )
            )
        ).scalars().all()
    )
    place = 1
    while place in taken:
        place += 1
    return place


class ShipError(Refusal):
    pass


class NotAboard(ShipError):
    """The body is not aboard. A ship is commanded from inside, not from the pier."""


class NotYours(ShipError):
    """Somebody else's ship. Shares between builders are a contract, not the
    engine's arithmetic (D-116)."""


class NoFoundation(ShipError):
    """No foundation in hand: a ship is materials, not an intention."""


class NoPort(ShipError):
    """No spaceport here: there is nothing to couple to."""


class NotEnoughThrust(ShipError):
    """Thrust against mass is below `ship.min_thrust_ratio`: it does not tear off."""


class NoLifeSupport(ShipError):
    """More people aboard than the life support holds."""


class NoFuel(ShipError):
    """Not enough fuel for the passage."""


class InFlight(ShipError):
    """The ship is undocked already: there is no edge to remove twice."""


class Docked(ShipError):
    """The ship is in port. Undock first -- the gangway is not flown away with."""


class TooFar(ShipError):
    """The weakest engine's class is below the route's (D-037, D-054)."""


# --- who belongs to what -----------------------------------------------------


def is_aboard(node: Node) -> bool:
    """Whether this node is part of a ship. Land is land."""
    return bool((node.properties or {}).get(ABOARD))


async def of_node(session: AsyncSession, node: Node) -> Ship | None:
    """Which ship this node belongs to -- or none, if it is ground.

    Membership is the `parent` hierarchy, the same one a city has over its
    locations (D-097): no second way to say "this node is part of that group".
    """
    if not is_aboard(node) or node.parent_id is None:
        return None
    return (
        await session.execute(select(Ship).where(Ship.node_id == node.parent_id))
    ).scalars().first()


async def nodes_of(session: AsyncSession, ship: Ship) -> list[Node]:
    """The nodes aboard: children of the group's delegate node."""
    return list(
        (
            await session.execute(
                select(Node).where(Node.parent_id == ship.node_id).order_by(Node.created_at)
            )
        ).scalars().all()
    )


async def ships_of(session: AsyncSession, identity_id: uuid.UUID) -> list[Ship]:
    """Whose ships these are. Ownership is personal: nodes aboard bear no title (D-198)."""
    return list(
        (
            await session.execute(
                select(Ship)
                .where(Ship.owner_identity_id == identity_id)
                .order_by(Ship.created_at)
            )
        ).scalars().all()
    )


async def aboard_of(session: AsyncSession, body: Body) -> Ship | None:
    """The ship the body is standing in, if it is standing in one at all."""
    node = await session.get(Node, body.node_id)
    return None if node is None else await of_node(session, node)


async def crew_of(session: AsyncSession, ship: Ship) -> list[Body]:
    """Living bodies aboard. A guest counts as crew: life support does not ask for a pass."""
    nodes = await nodes_of(session, ship)
    if not nodes:  # pragma: no cover -- a ship always has its connector
        return []
    return list(
        (
            await session.execute(
                select(Body).where(
                    Body.node_id.in_([node.id for node in nodes]),
                    Body.state == BodyState.ALIVE,
                )
            )
        ).scalars().all()
    )


# --- thrust, mass and what follows from them ---------------------------------


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
        await session.execute(
            select(Container).where(
                Container.kind == ContainerKind.NODE,
                Container.owner_id.in_([node.id for node in nodes]),
            )
        )
    ).scalars().all()
    if not yards:
        return []

    outer = list(
        (
            await session.execute(
                select(Item).where(Item.container_id.in_([yard.id for yard in yards]))
            )
        ).scalars().all()
    )
    inner_ = (
        await session.execute(
            select(Container).where(
                Container.kind == ContainerKind.STORAGE,
                Container.owner_id.in_([thing.id for thing in outer]),
            )
        )
    ).scalars().all()
    if not inner_:
        return outer
    inside = (
        await session.execute(
            select(Item).where(Item.container_id.in_([box.id for box in inner_]))
        )
    ).scalars().all()
    return outer + list(inside)


async def mass(
    session: AsyncSession, constants: Constants, catalog: Catalog, ship: Ship
) -> float:
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


async def engine_class(
    session: AsyncSession, constants: Constants, ship: Ship
) -> int | None:
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


async def life_support(
    session: AsyncSession, constants: Constants, ship: Ship
) -> int:
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


async def ratio(
    session: AsyncSession, constants: Constants, catalog: Catalog, ship: Ship
) -> float:
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
        await session.execute(
            select(Node).where(
                Node.layer == Layer.SPACE,
                Node.planet == planet,
                Node.parent_id.is_(None),
            )
        )
    ).scalars().first()


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


# --- building ----------------------------------------------------------------


async def _foundation_at_hand(session: AsyncSession, body: Body) -> list[Item]:
    pocket = await world.body_container(session, body)
    return list(
        (
            await session.execute(
                select(Item).where(
                    Item.container_id == pocket.id,
                    Item.type_key.in_(world.station_names(FOUNDATION)),
                )
            )
        ).scalars().all()
    )


async def _spend(session: AsyncSession, stacks: list[Item], quantity: float) -> float:
    """Write off this much from these stacks. Returns what could be taken.

    The stacks are locked first: the foundation in a pocket and the fuel in
    the rooms are shared with whoever carries them at the same moment."""
    locked = await world.lock_items(session, stacks)
    return amount_float(await world.consume(session, locked, to_amount(quantity)))


async def found(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    name: str,
    *,
    now: datetime | None = None,
) -> Job:
    """Lay a ship's foundation at a spaceport. The node arrives on schedule.

    The foundation is written off up front, like batch materials: work that
    lacked material does not start at all. What appears at the deadline is the
    base -- the connector and the docking point in one node.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise ShipError("мёртвое тело кораблей не закладывает")
    await travel.require_here(session, body)

    title = name.strip()
    if not title:
        raise ShipError("у корабля должно быть имя")

    port = await session.get(Node, body.node_id)
    if port is None:  # pragma: no cover -- a body always stands in a node
        raise ShipError("тело вне узла")
    if not await world.has_station(session, port, SPACEPORT):
        raise NoPort(
            "основание корабля закладывают на космодроме: причалить больше некуда"
        )
    #: Not onto another ship, even one carrying a spaceport aboard: that would
    #: be a second ship welded to the first for good, and ship-to-ship docking
    #: is a question of design, not a side effect (D-201). A ship is grown from
    #: the inside -- `extend`.
    if is_aboard(port):
        raise NoPort(
            "к борту новый корабль не закладывают: основание кладут на "
            "космодроме планеты, а борт расширяют изнутри"
        )
    return await _lay(session, constants, body, port, ship=None, name=title, now=moment)


async def extend(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    now: datetime | None = None,
) -> Job:
    """Lay one more node aboard, joined to the one it is laid from.

    The same action and the same item as the foundation (D-202): the ship grows
    by a node at a time, and its shape is the owner's decision.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise ShipError("мёртвое тело кораблей не строит")
    await travel.require_here(session, body)

    here = await session.get(Node, body.node_id)
    if here is None:  # pragma: no cover
        raise ShipError("тело вне узла")
    ship = await of_node(session, here)
    if ship is None:
        raise NotAboard(
            "корабль расширяют с борта: встаньте в узел корабля. "
            "Первый узел закладывают на космодроме"
        )
    if ship.owner_identity_id != body.identity_id:
        raise NotYours("это чужой корабль: строят у себя")
    return await _lay(session, constants, body, here, ship=ship, name=None, now=moment)


async def _lay(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    at: Node,
    *,
    ship: Ship | None,
    name: str | None,
    now: datetime,
) -> Job:
    """Common part of the two layings: check, write off, queue."""
    stacks = await _foundation_at_hand(session, body)
    in_hands = sum(amount_float(stack.amount) for stack in stacks)
    if in_hands + _EPS < 1:
        raise NoFoundation(
            f"нужна «{FOUNDATION}», а её в руках нет: корабль — это материалы, "
            "а не намерение"
        )
    await _spend(session, stacks, 1)

    ready_ = now + timedelta(hours=constants[R.SHIP_FOUNDATION_HOURS])
    event = await events.record(
        session,
        EventKind.SHIP_KEEL_LAID,
        actor_identity_id=body.identity_id,
        node_id=at.id,
        ship_id=None if ship is None else str(ship.id),
        name=name,
        ready_at=ready_.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.SHIP_KEEL,
        ready_,
        payload={
            "at": str(at.id),
            "ship": None if ship is None else str(ship.id),
            "name": name,
            "owner": str(body.identity_id),
        },
        dedup_key=f"ship.keel:{at.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise ShipError("закладка уже поставлена")
    return job


@handler(JobKind.SHIP_KEEL)
async def keel_laid(session: AsyncSession, job: Job) -> None:
    """The node aboard is ready: it appears together with its edge.

    A node without an edge would be a piece of map nobody can reach, so the two
    are one action: the base couples to the port, every other node to the one
    it was laid from.
    """
    from src.constants import current

    constants = current()
    at = await session.get(Node, uuid.UUID(job.payload["at"]))
    if at is None:  # pragma: no cover -- nodes do not vanish
        raise ShipError(f"закладка {job.id}: узла нет")
    owner = uuid.UUID(job.payload["owner"])

    raw_ship = job.payload.get("ship")
    ship = None if raw_ship is None else await session.get(Ship, uuid.UUID(raw_ship))
    if raw_ship is not None and ship is None:  # pragma: no cover
        raise ShipError(f"закладка {job.id}: корабля нет")

    if ship is None:
        ship, node = await _found_ship(
            session, constants, at, owner=owner, name=str(job.payload.get("name") or "Корабль")
        )
        kind, joined = EventKind.SHIP_FOUNDED, at.key
    else:
        node = await _add_node(session, constants, ship, at, owner=owner)
        kind, joined = EventKind.SHIP_EXTENDED, at.key

    await events.record(
        session,
        kind,
        actor_identity_id=owner,
        node_id=node.id,
        ship_id=str(ship.id),
        name=ship.name,
        node=node.key,
        joined_to=joined,
        nodes=len(await nodes_of(session, ship)),
    )


async def _found_ship(
    session: AsyncSession,
    constants: Constants,
    port: Node,
    *,
    owner: uuid.UUID,
    name: str,
) -> tuple[Ship, Node]:
    """A new ship: the delegate node, the connector and the edge to the port."""
    delegate = await world.create_node(
        session,
        f"ship.{uuid.uuid4().hex}",
        name,
        planet=port.planet,
        area_m2=constants[R.SHIP_NODE_AREA],
        layer=Layer.SPACE,
        parent=await _planet_root(session, port),
        properties={ABOARD: True},
    )
    connector = await _node_aboard(
        session, constants, delegate, "Основание", owner=owner, planet=port.planet
    )

    ship = Ship(
        name=name,
        owner_identity_id=owner,
        node_id=delegate.id,
        connector_node_id=connector.id,
        docked_node_id=port.id,
        berth=await _free_berth(session, port),
    )
    session.add(ship)
    await session.flush()

    #: The gangway: as long as the berth's number, and a road like any other --
    #: paved, because a pier is not a trail.
    await travel.connect(
        session,
        port,
        connector,
        base_seconds=_gangway_seconds(constants, ship.berth),
        surface=Surface.PAVED,
    )
    return ship, connector


async def _add_node(
    session: AsyncSession, constants: Constants, ship: Ship, at: Node, *, owner: uuid.UUID
) -> Node:
    """One more node aboard, joined to the one it was laid from."""
    delegate = await session.get(Node, ship.node_id)
    if delegate is None:  # pragma: no cover
        raise ShipError("у корабля нет группы")
    node = await _node_aboard(
        session, constants, delegate, "Отсек", owner=owner, planet=at.planet
    )
    #: A step between adjacent rooms is the shortest there is: inside a ship one
    #: walks as inside a city, and `travel.city_step` is that very step (D-045).
    await travel.connect(
        session,
        at,
        node,
        base_seconds=constants[R.TRAVEL_CITY_STEP].min,
        surface=Surface.PAVED,
    )
    return node


async def _node_aboard(
    session: AsyncSession,
    constants: Constants,
    delegate: Node,
    name: str,
    *,
    owner: uuid.UUID,
    planet: Planet,
) -> Node:
    """A node aboard: a room with an area, an owner and a building in it.

    **A building from the first second**, because machines are placed into a
    building and take its area (D-106): without it an engine would have nowhere
    to stand. And **an owner**, because a ship belongs to a person: nobody's
    node is open to all (D-198), and a stranger would carry the engine away.
    """
    node = await world.create_node(
        session,
        f"ship.node.{uuid.uuid4().hex}",
        name,
        planet=planet,
        area_m2=constants[R.SHIP_NODE_AREA],
        layer=Layer.LOCATION,
        parent=delegate,
        properties={ABOARD: True},
    )
    node.owner_identity_id = owner
    from src.engine import estate

    session.add(
        Building(
            node_id=node.id,
            area_m2=Decimal(str(constants[R.SHIP_NODE_AREA])),
            footprint_m2=Decimal(str(constants[R.SHIP_NODE_AREA])),
            floors=1,
            #: A hull is registered as a building only so that area and places
            #: are counted by one rule (D-106, D-202). Of the earthly types the
            #: dearest is the nearest -- a ship is metal and glass -- and decay
            #: passes it by: what keeps a ship up is not the weather over a yard.
            kind=estate.kinds(constants)[-1],
        )
    )
    await session.flush()
    return node


async def _planet_root(session: AsyncSession, node: Node) -> Node | None:
    """The planet the node stands on -- the ship hangs on it as a group."""
    current_node = node
    while current_node.parent_id is not None:
        parent = await session.get(Node, current_node.parent_id)
        if parent is None:  # pragma: no cover
            return None
        if parent.layer is Layer.SPACE:
            return parent
        current_node = parent
    return None


# --- docking, flight, docking ------------------------------------------------


async def _commanded_by(session: AsyncSession, body: Body, ship: Ship) -> None:
    """Who may move the ship: its owner, standing aboard.

    A guest aboard is carried away and cannot object -- that is deliberate
    (D-201): a ban would mean any stranger blocks a passage by standing in the
    hold. The dispute is a matter for the court (D-166), not for the engine.
    """
    if body.state is not BodyState.ALIVE:
        raise ShipError("мёртвое тело кораблём не управляет")
    await travel.require_here(session, body)
    if ship.owner_identity_id != body.identity_id:
        raise NotYours("это чужой корабль")
    aboard = await aboard_of(session, body)
    if aboard is None or aboard.id != ship.id:
        raise NotAboard("кораблём управляют с борта: поднимитесь на него")


async def undock(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    ship: Ship,
    *,
    now: datetime | None = None,
) -> Ship:
    """Cast off: the edge to the port is removed, and that is the flight (D-201).

    Four refusals, and every one of them is known before the attempt: not
    enough thrust for the mass, more people aboard than the life support holds,
    not enough fuel even for the way back, and somebody walking the gangway
    right now -- one does not pull it from under a walker.
    """
    moment = now or datetime.now(UTC)
    await _commanded_by(session, body, ship)
    if ship.docked_node_id is None:
        raise InFlight("корабль уже отстыкован")

    port = await session.get(Node, ship.docked_node_id)
    connector = await session.get(Node, ship.connector_node_id)
    if port is None or connector is None:  # pragma: no cover
        raise ShipError("у корабля нет коннектора или порта")

    thrust_ratio = await ratio(session, constants, catalog, ship)
    floor = constants[R.SHIP_MIN_THRUST_RATIO]
    if thrust_ratio < floor:
        raise NotEnoughThrust(
            f"тяги {thrust_ratio:.2f} на килограмм при нужных {floor:.2f}: "
            "корабль не отрывается. Ставьте двигатели или снимайте груз"
        )
    crew = len(await crew_of(session, ship))
    holds = await life_support(session, constants, ship)
    if crew > holds:
        raise NoLifeSupport(
            f"на борту {crew} человек, а жизнеобеспечение держит {holds}: "
            "ставьте ещё систему"
        )

    #: Fuel for at least the way back to this very port. An undocked ship has
    #: no edge to it at all, so nobody can bring fuel out to it and nobody
    #: aboard can walk off: casting off without a passage in the tanks would be
    #: a trap with no way out, and this world does not build those (pillar P6).
    #: The return hop is the cheapest passage there is, so affording it
    #: guarantees at least one way home.
    weight = await mass(session, constants, catalog, ship)
    table = await base_hours(
        session, constants, connector.planet, port.planet, at=moment
    )
    back = fuel_for(constants, weight, passage_hours(constants, table or 0, thrust_ratio))
    if await fuel_aboard(session, ship) + _EPS < back:
        raise NoFuel(
            f"на возврат в этот же порт нужно {back:.1f} «{FUEL}», а столько на "
            "борту нет: отстыкованный корабль недостижим, и топливо ему не привезут"
        )

    #: The whole undocking. `travel.disconnect` refuses if somebody is walking
    #: the edge, and that refusal travels up as it is.
    await travel.disconnect(session, port, connector)
    ship.docked_node_id = None
    #: The berth is given back with the gangway: a ship in flight holds no place
    #: at a pier, and the next arrival gets this one rather than a longer walk.
    ship.berth = None
    await session.flush()

    await events.record(
        session,
        EventKind.SHIP_UNDOCKED,
        actor_identity_id=body.identity_id,
        node_id=port.id,
        ship_id=str(ship.id),
        name=ship.name,
        port=port.key,
        crew=crew,
        ratio=round(thrust_ratio, ROUND_RATIO),
    )
    return ship


async def fly(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    ship: Ship,
    port: Node,
    *,
    now: datetime | None = None,
) -> Job:
    """Set out for a spaceport. Fuel now, arrival by a journal job.

    The route's class is decided by the **weakest** engine aboard (D-037): one
    poor engine in the cluster holds the cluster back.
    """
    moment = now or datetime.now(UTC)
    await _commanded_by(session, body, ship)
    if ship.docked_node_id is not None:
        raise Docked("корабль пристыкован: сначала отстыкуйтесь")
    if not await world.has_station(session, port, SPACEPORT):
        raise NoPort(f"в «{port.name}» нет космодрома: причалить не к чему")
    if is_aboard(port):  # pragma: no cover -- a port is never a ship node
        raise NoPort("к борту не причаливают: цель рейса — космодром")

    connector = await session.get(Node, ship.connector_node_id)
    if connector is None:  # pragma: no cover
        raise ShipError("у корабля нет коннектора")

    #: The time is settled **here**, at the moment of casting off, and is not
    #: recomputed afterwards: otherwise the sky would turn under a ship already
    #: under way and the passage would grow longer than the one paid for.
    table = await base_hours(
        session, constants, connector.planet, port.planet, at=moment
    )
    if table is None:
        raise TooFar(
            f"маршрута {connector.planet.value} — {port.planet.value} в мире нет"
        )
    need_class = route_class(constants, connector.planet, port.planet)
    have_class = await engine_class(session, constants, ship)
    if have_class is None:
        raise NotEnoughThrust("на корабле нет ни одного двигателя")
    if have_class < need_class:
        raise TooFar(
            f"маршрут требует двигателя {need_class} класса, а слабейший на "
            f"борту — {have_class}: класс задаёт самое слабое звено"
        )

    thrust_ratio = await ratio(session, constants, catalog, ship)
    floor = constants[R.SHIP_MIN_THRUST_RATIO]
    if thrust_ratio < floor:
        raise NotEnoughThrust(
            f"тяги {thrust_ratio:.2f} на килограмм при нужных {floor:.2f}: "
            "с такой массой рейс не начинается"
        )

    hours = passage_hours(constants, table, thrust_ratio)
    weight = await mass(session, constants, catalog, ship)
    need_fuel = fuel_for(constants, weight, hours)
    have_fuel = await fuel_aboard(session, ship)
    if have_fuel + _EPS < need_fuel:
        raise NoFuel(
            f"на рейс нужно {need_fuel:.1f} «{FUEL}», а на борту {have_fuel:.1f}"
        )
    burnt = await _spend(
        session,
        [
            thing
            for thing in await _things(session, ship)
            if thing.type_key in world.station_names(FUEL)
        ],
        need_fuel,
    )

    arrives = moment + timedelta(hours=hours)
    event = await events.record(
        session,
        EventKind.SHIP_LAUNCHED,
        actor_identity_id=body.identity_id,
        node_id=connector.id,
        ship_id=str(ship.id),
        name=ship.name,
        to=port.key,
        hours=round(hours, ROUND_HOURS),
        fuel=burnt,
        mass=round(weight, ROUND_MASS),
        ratio=round(thrust_ratio, ROUND_RATIO),
        arrives_at=arrives.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.SHIP_FLIGHT,
        arrives,
        payload={"ship": str(ship.id), "to": str(port.id)},
        dedup_key=f"ship.flight:{ship.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise ShipError("рейс уже поставлен")
    return job


@handler(JobKind.SHIP_FLIGHT)
async def arrived(session: AsyncSession, job: Job) -> None:
    """The passage is over: the edge to the port appears, and one may walk aboard again."""
    from src.constants import current

    ship = await session.get(Ship, uuid.UUID(job.payload["ship"]))
    port = await session.get(Node, uuid.UUID(job.payload["to"]))
    if ship is None or port is None:  # pragma: no cover
        raise ShipError(f"рейс {job.id} ведёт в никуда")
    connector = await session.get(Node, ship.connector_node_id)
    if connector is None:  # pragma: no cover
        raise ShipError("у корабля нет коннектора")

    #: The berth is taken on arrival, and it is whichever is free **there**:
    #: a ship does not carry its place from the port it left.
    ship.berth = await _free_berth(session, port)
    await travel.connect(
        session,
        port,
        connector,
        base_seconds=_gangway_seconds(current(), ship.berth),
        surface=Surface.PAVED,
    )
    ship.docked_node_id = port.id
    await _moor_to(session, ship, port)
    await session.flush()

    await events.record(
        session,
        EventKind.SHIP_DOCKED,
        actor_identity_id=ship.owner_identity_id,
        node_id=port.id,
        ship_id=str(ship.id),
        name=ship.name,
        port=port.key,
    )


async def _moor_to(session: AsyncSession, ship: Ship, port: Node) -> None:
    """The ship's nodes take the planet of the port it now stands at.

    Nodes aboard need a planet -- day length and environment wear are counted
    from it (D-201) -- and it must be the planet the ship is **actually** at
    rather than the one it was laid down at. Otherwise a ship that flew to
    Aurora would price its way home as a local hop between two Terran ports:
    the route is chosen by the pair of planets, and one of the pair would be a
    memory of the shipyard.

    The group moves with it: the delegate node hangs under the planet it is at,
    so the map shows the ship where it is.
    """
    delegate = await session.get(Node, ship.node_id)
    root = await _planet_root(session, port)
    aboard = await nodes_of(session, ship)
    for node in [*aboard, *([delegate] if delegate is not None else [])]:
        node.planet = port.planet
    if delegate is not None and root is not None:
        delegate.parent_id = root.id
    await session.flush()


# --- what the client shows before the attempt --------------------------------


async def in_sight(
    session: AsyncSession, constants: Constants, node: Node
) -> dict[str, list[dict[str, object]]] | None:
    """What of ships is visible from this node, and nothing beyond it.

    A ship's interior is **not on the public map** (D-201). From the pier a ship
    is one hull, and how many cabins it holds, what is joined to what and where
    the hold is stays unknown -- that is the whole point of the single
    connector: nothing is seen past the gangway. So what a ship shows travels
    with the look of whoever stands close enough, and only what they may see:

    * **at a spaceport** -- the ships moored to it, each as one node with its
      gangway. That is the door, not the inside: it appears on walking up to the
      pier and is gone on walking away from it;
    * **aboard** -- the rooms and the ways between them, because from inside a
      ship is an ordinary piece of the graph one walks around.

    None means neither: ordinary ground with no ship within sight.
    """
    if is_aboard(node):
        return await _from_aboard(session, constants, node)
    return await _from_pier(session, constants, node)


async def _from_pier(
    session: AsyncSession, constants: Constants, port: Node
) -> dict[str, list[dict[str, object]]] | None:
    """Ships moored here: a door apiece, on the layer the pier itself is on.

    A moored ship stands in the city as a building does -- one walks up to it
    and up its gangway -- so that is where the map shows it, under the same city
    as the port. Its own layer stays what it is; this is the delegate's trick
    the map has used from the start (D-045), not a second kind of node.
    """
    moored = (
        await session.execute(select(Ship).where(Ship.docked_node_id == port.id))
    ).scalars().all()
    if not moored:
        return None

    city = None if port.parent_id is None else await session.get(Node, port.parent_id)
    nodes: list[dict[str, object]] = []
    edges: list[dict[str, object]] = []
    for ship in moored:
        connector = await session.get(Node, ship.connector_node_id)
        if connector is None:  # pragma: no cover -- a ship always has one
            continue
        nodes.append(
            {
                "key": connector.key,
                #: The ship's name, not the compartment's: from the pier one
                #: sees «Заря», and what its first room is called is a thing
                #: learnt aboard.
                "name": ship.name,
                "layer": port.layer.value,
                "parent": None if city is None else city.key,
                "ring": (port.properties or {}).get("кольцо"),
                "exit": False,
                "port": False,
                "planet": connector.planet.value,
                "orbit": None,
                "deferred": False,
                "aboard": True,
                "flight": None,
            }
        )
        gangway = await travel._edge_between(session, port.id, connector.id)
        if gangway is not None:
            edges.append(
                {
                    "a": port.key,
                    "b": connector.key,
                    "surface": gangway.surface.value,
                    "seconds": round(travel.edge_seconds(constants, gangway)),
                }
            )
    return {"nodes": nodes, "edges": edges} if nodes else None


async def _from_aboard(
    session: AsyncSession, constants: Constants, node: Node
) -> dict[str, list[dict[str, object]]] | None:
    """The ship one is standing in: its rooms and the ways between them."""
    ship = await of_node(session, node)
    if ship is None:  # pragma: no cover -- an aboard node always has its ship
        return None

    rooms = await nodes_of(session, ship)
    delegate = await session.get(Node, ship.node_id)
    keys = {room.id: room.key for room in rooms}
    if delegate is not None:
        keys[delegate.id] = delegate.key
    #: The gangway too, when there is one: from inside the way out is a fact of
    #: the graph like any other, and without it the interior hangs on nothing.
    port = (
        None if ship.docked_node_id is None else await session.get(Node, ship.docked_node_id)
    )
    if port is not None:
        keys[port.id] = port.key

    ways = (
        await session.execute(
            select(Edge).where(or_(Edge.node_a_id.in_(keys), Edge.node_b_id.in_(keys)))
        )
    ).scalars().all()
    return {
        "nodes": [
            {
                "key": room.key,
                "name": room.name,
                "layer": room.layer.value,
                "parent": None if delegate is None else delegate.key,
                "ring": None,
                "exit": False,
                "port": False,
                "planet": room.planet.value,
                "orbit": None,
                "deferred": False,
                "aboard": True,
                "flight": None,
            }
            for room in rooms
        ],
        "edges": [
            {
                "a": keys[edge.node_a_id],
                "b": keys[edge.node_b_id],
                "surface": edge.surface.value,
                "seconds": round(travel.edge_seconds(constants, edge)),
            }
            for edge in ways
            if edge.node_a_id in keys and edge.node_b_id in keys
        ],
    }


async def passages(session: AsyncSession) -> dict[uuid.UUID, dict[str, object]]:
    """Ships under way: the delegate node -> where it goes and when it arrives.

    A ship in flight is **nowhere in the graph**: undocking removes its only
    edge (D-201), so its place cannot be read off the map the way everything
    else can. It is known to one thing only -- the job that will bring the ship
    in -- and that job holds both ends of the passage: it was created at
    departure and fires on arrival. The share between the two is exactly how
    far the ship has got, and that is what the space layer draws.

    Keyed by the delegate node, because that is what the map speaks in.
    """
    flights = (
        await session.execute(
            select(Job).where(
                Job.kind == JobKind.SHIP_FLIGHT, Job.state == JobState.PENDING
            )
        )
    ).scalars().all()
    if not flights:
        return {}

    afloat = {
        str(ship.id): ship
        for ship in (
            await session.execute(select(Ship).where(Ship.docked_node_id.is_(None)))
        ).scalars().all()
    }
    under_way: dict[uuid.UUID, dict[str, object]] = {}
    for job in flights:
        ship = afloat.get(str(job.payload.get("ship")))
        if ship is None:
            continue
        under_way[ship.node_id] = {
            "to": uuid.UUID(str(job.payload["to"])),
            "started_at": job.created_at,
            "arrives_at": job.run_at,
        }
    return under_way


async def ports(session: AsyncSession) -> list[Node]:
    """Every node with a spaceport. What a ship may aim at at all."""
    return list(
        (
            await session.execute(
                select(Node)
                .join(Container, Container.owner_id == Node.id)
                .join(Item, Item.container_id == Container.id)
                .where(
                    Container.kind == ContainerKind.NODE,
                    Item.type_key.in_(world.station_names(SPACEPORT)),
                )
                .distinct()
            )
        ).scalars().all()
    )


async def profile(
    session: AsyncSession, constants: Constants, catalog: Catalog, ship: Ship
) -> dict:
    """The ship's summary: thrust, mass, and the price of every route from here.

    Shown **before** undocking, deliberately (D-202): a refusal by mass must not
    be a surprise sprung after the hold is loaded. Remote, like every reading:
    information travels the Net, matter requires presence (D-044).
    """
    nodes = await nodes_of(session, ship)
    weight = await mass(session, constants, catalog, ship)
    pull = await thrust(session, constants, ship)
    thrust_ratio = pull / weight if weight > 0 else 0.0
    have_class = await engine_class(session, constants, ship)
    crew = len(await crew_of(session, ship))
    connector = await session.get(Node, ship.connector_node_id)
    docked = (
        None if ship.docked_node_id is None else await session.get(Node, ship.docked_node_id)
    )

    #: The prices are for **this** moment: the sky turns, and a route quoted an
    #: hour ago is not the route one gets. The player sees what casting off now
    #: would cost, and the window they may prefer to wait for is on the map.
    moment = datetime.now(UTC)
    routes: list[dict] = []
    for port in await ports(session):
        if docked is not None and port.id == docked.id:
            continue
        planet = Planet.TERRA if connector is None else connector.planet
        table = await base_hours(session, constants, planet, port.planet, at=moment)
        if table is None:
            continue
        need_class = route_class(constants, planet, port.planet)
        hours = passage_hours(constants, table, thrust_ratio) if thrust_ratio > 0 else None
        routes.append(
            {
                "node": port.key,
                "name": port.name,
                "planet": port.planet.value,
                "class": need_class,
                "hours": None if hours is None else round(hours, ROUND_HOURS),
                "fuel": (
                    None
                    if hours is None
                    else round(fuel_for(constants, weight, hours), ROUND_MASS)
                ),
                #: Why exactly it is unavailable -- the class or the thrust. A
                #: bare "unavailable" leaves nothing to act on.
                "reachable": (
                    hours is not None
                    and have_class is not None
                    and have_class >= need_class
                    and thrust_ratio >= constants[R.SHIP_MIN_THRUST_RATIO]
                ),
            }
        )

    return {
        "ship": str(ship.id),
        "name": ship.name,
        "nodes": len(nodes),
        "mass": round(weight, ROUND_MASS),
        "thrust": round(pull, ROUND_MASS),
        "ratio": round(thrust_ratio, ROUND_RATIO),
        "min_ratio": constants[R.SHIP_MIN_THRUST_RATIO],
        "class": have_class,
        "crew": crew,
        "life_support": await life_support(session, constants, ship),
        "fuel": round(await fuel_aboard(session, ship), ROUND_MASS),
        "docked": None if docked is None else docked.key,
        "port": None if docked is None else docked.name,
        #: Which berth of that port, and therefore how long the gangway is: a
        #: busy yard boards you further from the door (D-201).
        "berth": ship.berth,
        "connector": None if connector is None else connector.key,
        "routes": sorted(routes, key=lambda route: (not route["reachable"], route["name"])),
    }
