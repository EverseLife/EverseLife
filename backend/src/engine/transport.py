# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Transport: how what cannot be carried in hands is hauled (D-107, D-129, D-157).

Cargo got mass (D-146), and the carry limit started working: `inventory.carry_mass`
in hands, everything above -- "only by vehicle". But there was no vehicle,
and worse -- a wagon is heavier than the limit itself, i.e. it is not taken in
hand at all. The carter's profession, which all the logistics was written
for, did not exist for a single day.

## Three states and not one more

| State | What it means |
|---|---|
| **standing** | an item `kind: vehicle` lies in the node, like a machine. Never taken in hand |
| **harnessed** | the body pulls it along all transits. One at a time, and only what is nearby |
| **loaded** | cargo rides **in the hold** up to `transport.capacity` kilograms, not in hands |

**Why a hold rather than a bonus to hands.** A bonus would be simpler -- a
backpack works exactly so via `inventory.carry_bonus`. But then the cargo of a
carter who unharnessed would teleport into their hands or evaporate, while
forty sacks of grain must stay in the wagon where it stopped. A separate
container is the only way in which "abandon a loaded convoy" is a normal move
of the game rather than an engine error (D-157).

## What the road decides (D-107)

| Surface | Vehicle |
|---|---|
| offroad | does not pass at all: there one walks and carries |
| road | a light one passes, up to `transport.heavy_from` |
| paved highway | any passes |

Hence the main consequence: **the road is a precondition of trade, not a
convenience.** A cart will not reach a node found by exploration (D-156) until
a road is laid to it; autopath with a convoy is built over passable edges and
stops at the last node if the surface does not let it further.

## What is not here and why

* **Fodder and fuel.** `transport.upkeep_per_leg` is given in money per leg,
  and money in this world has nowhere to vanish (I2): a posting must have a
  second side, and there is none yet. Fodder as an item is not in the vault
  data either;
* **Volume.** `transport.volume_per_mass` waits for items to have volume --
  nothing has it, and inventing data in code is forbidden (D-065);
* **Crew and convoy.** `transport.crew_ship` waits for ships, and a shared
  convoy of several carters for its own mechanic.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.engine import events, world
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.travel import Harness
from src.models.world import Node, Surface
from src.units import amount_float


class TransportError(Exception):
    pass


class NotVehicle(TransportError):
    """Not a vehicle. One harnesses to a wagon, not a sack of grain."""


class NotHere(TransportError):
    """No vehicle in this node. Matter requires presence (D-044)."""


class AlreadyHarnessed(TransportError):
    """Already harnessed. One body does not pull two convoys."""


class NotHarnessed(TransportError):
    """The body is not harnessed: nothing to load and nowhere."""


class Overloaded(TransportError):
    """The hold is full. Nobody carries more than the capacity."""


class Impassable(TransportError):
    """The surface does not let the vehicle through (D-107): offroad -- on foot and on your back."""


def is_vehicle(catalog: Catalog, type_key: str) -> bool:
    """Whether this is a vehicle. The sign is `kind: vehicle` from the vault, not the name
    (D-090)."""
    try:
        return catalog.recipes.recipe(type_key).kind is ItemKind.VEHICLE
    except Exception:  # noqa: BLE001 -- raw material has no recipe, and that is normal
        return False


def word(constants: Constants, type_key: str) -> str | None:
    """The word the vault calls this vehicle by.

    The keys of `transport.speed_k` are words ("тачка", "повозка", "судно"),
    and the vehicle's thing class names exactly that word (D-215): a wagon is
    of class "Повозка". The engine keeps no type list: add an airship class in
    the vault and it flies without a code change. Substring matching over item
    names is gone -- it classified anything whose name merely contained the word.
    """
    from src.constants.catalog import current_catalog

    thing_class = current_catalog().recipes.class_of(type_key)
    #: No class -- the thing's own name may be the table word itself ("Судно"):
    #: that keeps the tables usable before a recipe for such a vehicle exists.
    label = (thing_class or type_key).lower()
    return label if label in constants[R.TRANSPORT_SPEED_K] else None


def capacity(constants: Constants, type_key: str) -> float:
    """Hold capacity, kg. The vault does not know such a vehicle -- refusal."""
    label = word(constants, type_key)
    holds = constants[R.TRANSPORT_CAPACITY]
    if label is None or label not in holds:
        raise NotVehicle(
            f"вольт не знает грузоподъёмности «{type_key}»: заведите его в "
            "transport.capacity и transport.speed_k"
        )
    return holds[label]


def speed(constants: Constants, type_key: str) -> float:
    """How many times faster than on foot. A barrow is slower than legs, and that is its price."""
    speeds = constants[R.TRANSPORT_SPEED_K]
    return speeds.get(word(constants, type_key), 1.0)


def heavy(constants: Constants, type_key: str) -> bool:
    """Whether the vehicle is heavy: such needs a paved highway (D-107)."""
    return capacity(constants, type_key) >= constants[R.TRANSPORT_HEAVY_FROM]


def passable(constants: Constants, surface: Surface, type_key: str) -> bool:
    """Whether such a vehicle passes over such a surface (D-107)."""
    if surface is Surface.TRAIL:
        return False
    if surface is Surface.PAVED:
        return True
    return not heavy(constants, type_key)


async def harnessed(session: AsyncSession, body: Body) -> Item | None:
    """What the body is harnessed to now, if harnessed."""
    line = (
        await session.execute(select(Harness).where(Harness.body_id == body.id))
    ).scalar_one_or_none()
    if line is None:
        return None
    return await session.get(Item, line.item_id)


async def harness(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body, item: Item
) -> Item:
    """Harness to a vehicle standing here. In person: a convoy is not teleported."""
    from src.engine import travel

    if body.state is not BodyState.ALIVE:
        raise TransportError("мёртвое тело никуда не впрягается")
    await travel.require_here(session, body)

    if not is_vehicle(catalog, item.type_key):
        raise NotVehicle(f"«{item.type_key}» — не транспорт: впрягаются в повозку")
    #: The refusal must come before harnessing, not on the first transit.
    capacity(constants, item.type_key)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body always stands in a node
        raise TransportError("тело вне узла")
    yard = await world.node_container(session, node)
    if item.container_id != yard.id:
        raise NotHere("транспорта нет в этом узле: впрягаются в то, что рядом")

    if await harnessed(session, body) is not None:
        raise AlreadyHarnessed("уже впряжён: сначала распрячься")
    foreign_ = (
        await session.execute(select(Harness).where(Harness.item_id == item.id))
    ).scalar_one_or_none()
    if foreign_ is not None:
        raise AlreadyHarnessed("в этот транспорт уже впряжены")

    session.add(Harness(body_id=body.id, item_id=item.id))
    await session.flush()
    await events.record(
        session,
        EventKind.TRANSPORT_HARNESSED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        item_id=str(item.id),
        type_key=item.type_key,
        capacity=capacity(constants, item.type_key),
    )
    return item


async def unharness(session: AsyncSession, body: Body) -> Item | None:
    """Unharness. The vehicle stays standing here together with the cargo."""
    line = (
        await session.execute(select(Harness).where(Harness.body_id == body.id))
    ).scalar_one_or_none()
    if line is None:
        return None
    item = await session.get(Item, line.item_id)
    await session.delete(line)
    await session.flush()
    if item is not None:
        await events.record(
            session,
            EventKind.TRANSPORT_UNHARNESSED,
            actor_identity_id=body.identity_id,
            node_id=body.node_id,
            item_id=str(item.id),
            type_key=item.type_key,
        )
    return item


async def drop_missing(session: AsyncSession, item_id: uuid.UUID) -> None:
    """Forget the harness if the vehicle is gone: broke or drove away."""
    line = (
        await session.execute(select(Harness).where(Harness.item_id == item_id))
    ).scalar_one_or_none()
    if line is not None:
        await session.delete(line)
        await session.flush()


async def cargo(session: AsyncSession, vehicle: Item) -> Container:
    """This vehicle's hold. Created on first need."""
    stmt = select(Container).where(
        Container.kind == ContainerKind.VEHICLE, Container.owner_id == vehicle.id
    )
    hold = (await session.execute(stmt)).scalar_one_or_none()
    if hold is None:
        hold = Container(kind=ContainerKind.VEHICLE, owner_id=vehicle.id)
        session.add(hold)
        await session.flush()
    return hold


async def cargo_items(session: AsyncSession, vehicle: Item) -> list[Item]:
    from src.engine import world

    return list(await world.contents(session, await cargo(session, vehicle)))


async def cargo_mass(
    session: AsyncSession, catalog: Catalog, vehicle: Item
) -> float:
    """How many kilograms the hold already carries."""
    from src.engine import gear

    return sum(
        gear.mass_of(catalog, thing.type_key, amount_float(thing.amount))
        for thing in await cargo_items(session, vehicle)
    )


async def fill(
    session: AsyncSession, constants: Constants, catalog: Catalog, vehicle: Item
) -> float:
    """How full the hold is, a share from 0 to 1. A full one wears twice as much."""
    limit = capacity(constants, vehicle.type_key)
    if limit <= 0:  # pragma: no cover -- a zero hold is refused at harnessing
        return 0.0
    return min(1.0, await cargo_mass(session, catalog, vehicle) / limit)


async def load(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    quantity: float | None = None,
) -> float:
    """Load from the hands into the hold. In person: nothing is moved while on the go."""
    from src.engine import gear, travel

    if body.state is not BodyState.ALIVE:
        raise TransportError("мёртвое тело ничего не грузит")
    await travel.require_here(session, body)

    wagon = await harnessed(session, body)
    if wagon is None:
        raise NotHarnessed("грузить некуда: сначала впрячься")
    pocket = await world.body_container(session, body)
    if item.container_id != pocket.id:
        raise TransportError("этой вещи нет в руках: грузят своё и из рук")

    qty = amount_float(item.amount) if quantity is None else quantity
    if qty <= 0:
        raise TransportError("грузить нечего")
    bonus = gear.mass_of(catalog, item.type_key, qty)
    free = capacity(constants, wagon.type_key) - await cargo_mass(
        session, catalog, wagon
    )
    if bonus > free:
        raise Overloaded(
            f"в трюме свободно {free:.1f} кг, а это {bonus:.1f} кг: "
            "больше грузоподъёмности не увезёт никто"
        )

    hold = await cargo(session, wagon)
    carried = await _move(session, item, hold, qty)
    await events.record(
        session,
        EventKind.TRANSPORT_LOADED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(wagon.id),
        type_key=item.type_key,
        amount=carried,
        mass=gear.mass_of(catalog, item.type_key, carried),
    )
    return carried


async def unload(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    quantity: float | None = None,
) -> float:
    """Unload from the hold into the hands. The hands limit does not go anywhere."""
    from src.engine import gear, travel

    if body.state is not BodyState.ALIVE:
        raise TransportError("мёртвое тело ничего не выгружает")
    await travel.require_here(session, body)

    wagon = await harnessed(session, body)
    if wagon is None:
        raise NotHarnessed("выгружать нечего: сначала впрячься")
    hold = await cargo(session, wagon)
    if item.container_id != hold.id:
        raise TransportError("этой вещи нет в трюме")

    qty = amount_float(item.amount) if quantity is None else quantity
    if qty <= 0:
        raise TransportError("выгружать нечего")
    await gear.check_carry(session, constants, catalog, body, item.type_key, qty)

    pocket = await world.body_container(session, body)
    carried = await _move(session, item, pocket, qty)
    await events.record(
        session,
        EventKind.TRANSPORT_UNLOADED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(wagon.id),
        type_key=item.type_key,
        amount=carried,
    )
    return carried


async def follow(session: AsyncSession, vehicle: Item, node: Node) -> None:
    """The convoy arrived in the node: the vehicle itself and its hold now stand here."""
    yard = await world.node_container(session, node)
    vehicle.container_id = yard.id
    hold = await cargo(session, vehicle)
    hold.node_id = node.id
    await session.flush()


async def spill(session: AsyncSession, vehicle: Item, node: Node) -> int:
    """Dump the cargo into the node. Matter does not vanish with what carried it."""
    yard = await world.node_container(session, node)
    things = await cargo_items(session, vehicle)
    for thing in things:
        thing.container_id = yard.id
        await world.stack_up(session, thing)
    await session.flush()
    return len(things)


async def wear_leg(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    vehicle: Item,
    node: Node,
) -> bool:
    """Write off wear for the leg. True -- the vehicle is finished by it.

    A full hold wears twice as much as an empty one: it is not air that is
    hauled, and `wear.transport_per_leg` is given "adjusted for load" (D-129).
    """
    from src.engine import wear

    load = await fill(session, constants, catalog, vehicle)
    price = constants[R.WEAR_TRANSPORT_PER_LEG] * (1 + load)

    #: Tidy up **before** the wagon disappears: the cargo and the harness
    #: reference it, and writing off wear first would drop the foreign key.
    #: The wagon runs out like every thing (pillar P2), but the cargo stays
    #: lying where the convoy stopped: a breakdown is a stop, not a loss of cargo (D-157).
    if not wear.wears_out(constants, vehicle, price):
        return await wear.spend(
            session,
            constants,
            vehicle,
            price,
            cause="переход с обозом",
            actor_identity_id=body.identity_id,
        )

    rolled = await spill(session, vehicle, node)
    await drop_missing(session, vehicle.id)
    await wear.spend(
        session,
        constants,
        vehicle,
        price,
        cause="переход с обозом",
        actor_identity_id=body.identity_id,
    )
    await events.record(
        session,
        EventKind.TRANSPORT_BROKE,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        type_key=vehicle.type_key,
        spilled=rolled,
    )
    return True


async def view(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body
) -> dict | None:
    """The convoy through the client's eyes: what it is harnessed to, what it carries and where
    it can pass."""
    wagon = await harnessed(session, body)
    if wagon is None:
        return None
    limit = capacity(constants, wagon.type_key)
    cargo = [
        {
            "id": str(thing.id),
            "type_key": thing.type_key,
            "amount": amount_float(thing.amount),
            "quality": None if thing.quality is None else float(thing.quality),
        }
        for thing in await cargo_items(session, wagon)
    ]
    return {
        "id": str(wagon.id),
        "type_key": wagon.type_key,
        "condition": float(wagon.condition),
        "capacity": limit,
        "mass": await cargo_mass(session, catalog, wagon),
        "speed_k": speed(constants, wagon.type_key),
        "heavy": heavy(constants, wagon.type_key),
        "cargo": cargo,
    }


async def _move(
    session: AsyncSession, item: Item, target: Container, quantity: float
) -> float:
    """Move a stack or part of it into the hold or out of it.

    The moving itself is common to the whole world (`world.move_stack`): the
    hold, the chest and the terminal must behave the same.
    """

    return await world.move_stack(session, item, target, quantity)
