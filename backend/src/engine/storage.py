# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Storage: chest, shelf and everything with `store` in the vault (D-181).

Carried load is bounded (D-146), the convoy hauls (D-157) -- yet until now
there was nowhere **to put things down**: possessions travelled in hands or
lay in the terminal, i.e. on sale. Home as a place where one stores starts here.

## What makes a thing a storage

A number in the vault, not a name in code: `store` is capacity in kilograms.
Add a new chest in the data and it works without an engine change (D-090).
Whether it is furniture or a machine the engine does not care: it looks at the field.

## Rules

* **in person** -- nobody reaches into somebody's chest from half a map away;
* **whoever may dispose of the node disposes** (`station.may_build`): the
  owner, and on civic land the authority. Breaking into somebody's chest is a
  matter for the court (D-166), not a button. The **floor** is the other way
  round (D-204): what lies loose is put down and picked up by anyone who got in,
  and a shut door is what keeps them out -- the chest is the protection inside an
  open location;
* **the limit is mass**, in the same kilograms as hands and hold: there is no
  third unit of capacity in the world;
* **a full storage is not carried away** (`station.take`): otherwise "take
  the furniture" would become a way to carry a ton of cargo in the pocket.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import access, energy, estate, events, gear, station, travel, world
from src.engine.errors import Refusal
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Node
from src.units import amount_float


class StorageError(Refusal):
    pass


class NotStorage(StorageError):
    """Not a storage: the thing has no capacity in the vault."""


class NotYours(StorageError):
    """Somebody else's chest is not opened: access follows the right to the node (D-181).

    The floor refuses for another reason -- a passer-by is not inside (D-204).
    """


class Full(StorageError):
    """No more fits. The limit is mass, as with hands and hold."""


def capacity(catalog: Catalog, type_key: str) -> float | None:
    """The thing's capacity as a storage, kg. `None` -- the thing is not a storage."""
    try:
        return catalog.recipes.recipe(type_key).store
    except Exception:  # noqa: BLE001 -- raw material has no recipe, and that is normal
        return None


def is_storage(catalog: Catalog, type_key: str) -> bool:
    limit = capacity(catalog, type_key)
    return limit is not None and limit > 0


#: The value of `Recipe.holds` that makes a storage a vessel (D-230).
LIQUID = "liquid"


def is_vessel(catalog: Catalog, type_key: str) -> bool:
    """A storage whose `holds` is `жидкость`: a canister, a tank (D-230)."""
    return is_storage(catalog, type_key) and catalog.recipes.holds_of(type_key) == LIQUID


def admits(catalog: Catalog, chest_key: str, type_key: str) -> bool:
    """Whether this storage takes this thing: a vessel takes liquids only, a
    chest takes everything but. One question for both doors (D-230)."""
    return is_vessel(catalog, chest_key) == catalog.recipes.is_liquid(type_key)


async def inside(session: AsyncSession, chest: Item, *, create: bool = True) -> Container | None:
    """The storage's inside. Created on first need -- by a write, never by a
    read: with `create=False` an empty chest has no container and is None."""
    stmt = select(Container).where(
        Container.kind == ContainerKind.STORAGE, Container.owner_id == chest.id
    )
    container = (await session.execute(stmt)).scalar_one_or_none()
    if container is None and create:
        container = Container(kind=ContainerKind.STORAGE, owner_id=chest.id)
        session.add(container)
        await session.flush()
    return container


async def content(session: AsyncSession, chest: Item) -> list[Item]:
    container = await inside(session, chest, create=False)
    return [] if container is None else list(await world.contents(session, container))


async def contents_of(session: AsyncSession, chests: Sequence[Item]) -> dict[uuid.UUID, list[Item]]:
    """What lies in each of several storages, in two queries rather than two
    per chest: the inventory reads every canister in the hands at every
    `look` (D-230), and the carry limit at every pick-up."""
    if not chests:
        return {}
    holds = (
        (
            await session.execute(
                select(Container).where(
                    Container.kind == ContainerKind.STORAGE,
                    Container.owner_id.in_([chest.id for chest in chests]),
                )
            )
        )
        .scalars()
        .all()
    )
    by_hold = {hold.id: hold.owner_id for hold in holds}
    found: dict[uuid.UUID, list[Item]] = {chest.id: [] for chest in chests}
    if by_hold:
        rows = await session.execute(
            select(Item).where(Item.container_id.in_(list(by_hold))).order_by(Item.id)
        )
        for thing in rows.scalars().all():
            found[by_hold[thing.container_id]].append(thing)
    return found


async def stored_mass(session: AsyncSession, catalog: Catalog, chest: Item) -> float:
    """How many kilograms already lie inside."""

    return sum(
        gear.mass_of(catalog, thing.type_key, amount_float(thing.amount))
        for thing in await content(session, chest)
    )


async def is_empty(session: AsyncSession, chest: Item) -> bool:
    """Whether it is empty inside. This decides whether the furniture is handed over."""
    container = await inside(session, chest, create=False)
    if container is None:
        return True
    found = await session.scalar(select(Item.id).where(Item.container_id == container.id).limit(1))
    return found is None


async def put(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    chest: Item,
    item: Item,
    quantity: float | None = None,
) -> float:
    """Put from the hands into the storage. In person and only into your own."""
    await _allowed(session, catalog, body, chest)

    pocket = await world.body_container(session, body)
    if item.container_id != pocket.id:
        raise StorageError(key="storage-not-in-hands")

    qty = amount_float(item.amount) if quantity is None else quantity
    if qty <= 0:
        raise StorageError(key="storage-nothing-to-put")
    #: A vessel takes liquids only, a chest everything but (D-230). A liquid
    #: never lies in the hands, so this door mostly refuses the other way: a
    #: canister into a tank, a chest into a canister.
    if not admits(catalog, chest.type_key, item.type_key):
        raise StorageError(
            key="storage-mismatch",
            goods=item.type_key,
            chest=chest.type_key,
            why="vessel" if is_vessel(catalog, chest.type_key) else "chest",
        )

    bonus = gear.mass_of(catalog, item.type_key, qty)
    limit = capacity(catalog, chest.type_key) or 0.0
    free = limit - await stored_mass(session, catalog, chest)
    if bonus > free:
        raise Full(key="storage-chest-full", chest=chest.type_key, free=free, mass=bonus)

    contents = await inside(session, chest)
    carried = await world.move_stack(session, item, contents, qty)
    await events.record(
        session,
        EventKind.STORAGE_PUT,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(chest.id),
        type_key=item.type_key,
        amount=carried,
    )
    return carried


async def take(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    chest: Item,
    item: Item,
    quantity: float | None = None,
) -> float:
    """Take from the storage into the hands. The hands limit does not go anywhere."""
    await _allowed(session, catalog, body, chest)

    contents = await inside(session, chest)
    if item.container_id != contents.id:
        raise StorageError(key="storage-not-in-storage")

    qty = amount_float(item.amount) if quantity is None else quantity
    if qty <= 0:
        raise StorageError(key="storage-nothing-to-take")

    await gear.check_carry(session, constants, catalog, body, item.type_key, qty)

    pocket = await world.body_container(session, body)
    carried = await world.move_stack(session, item, pocket, qty)
    await events.record(
        session,
        EventKind.STORAGE_TAKEN,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(chest.id),
        type_key=item.type_key,
        amount=carried,
    )
    return carried


# --- the floor of a place (D-192) --------------------------------------------


class NoRoom(StorageError):
    """No space left here. Area is finite: build more, use chests or haul away."""


async def lying(session: AsyncSession, node: Node, *, indoors: bool = True) -> list[Item]:
    """What lies loose on one of the node's two surfaces.

    What stands -- a machine or a chest put up -- is shown by its own window
    and pays for its place by slots (D-106); a machine dropped here lies among
    the sacks (D-278). `indoors` picks the surface: the floor of the house, or
    the open ground beside it (D-244).
    """

    inside, outside = await estate.split(session, node)
    return inside if indoors else outside


async def _require_inside(session: AsyncSession, node: Node, body: Body) -> None:
    """The floor is for those inside, not for those walking through (D-204).

    Passage through a shut location is free, and a body may end up standing in
    one -- the leg between two jobs, a route that broke where the edge vanished.
    Standing there it is not a guest but a passer-by: the floor is not its business.
    """

    if await access.may_enter(session, node, body.identity_id):
        return
    raise NotYours(key="storage-passing-through", node=node.name)


async def surface_of(session: AsyncSession, node: Node, indoors: bool | None) -> bool:
    """Which of the node's two surfaces a hand means (D-244). True is indoors.

    `indoors` is what the window asked for; left unsaid, the answer is the one
    a person would give without thinking -- indoors when there is a roof to step
    under, on the ground when there is not.
    """
    #: The storey underfoot, not the whole house (D-247): a floor above the
    #: ground carries no building record of its own, and asking `built_area`
    #: there answered "no roof" in a room with four walls.
    roofed = await estate.storey_area(session, node) > 0
    inside = roofed if indoors is None else bool(indoors)
    if inside and not roofed:
        raise NoRoom(key="storage-no-building")
    #: Upstairs there is no ground: under a storey is somebody's ceiling (D-247).
    if not inside and estate.storey_of(node) is not None:
        raise NoRoom(key="storage-storey-not-yard")
    return inside


async def drop(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    quantity: float | None = None,
    *,
    indoors: bool | None = None,
) -> float:
    """Put a thing down: on the floor of the house, or on the open ground (D-244).

    Cargo takes area (D-192), and area is finite -- that is what makes a
    warehouse a decision rather than a formality. Two surfaces means two
    budgets: the house's floor is what the house was built for, the yard is
    what is left of the plot around it, and a thing lies in one of them.
    Whoever got in may put things down (D-204): the door decides who is inside,
    not this check.
    """

    if body.state is not BodyState.ALIVE:
        raise StorageError(key="storage-dead-puts")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body without a node is a bug
        raise StorageError(key="storage-body-off-node")
    await _require_inside(session, node, body)
    pocket = await world.body_container(session, body)
    if item.container_id != pocket.id:
        raise StorageError(key="storage-hands-only")

    qty = amount_float(item.amount) if quantity is None else quantity
    if qty <= 0:
        raise StorageError(key="storage-nothing-to-put")

    inside = await surface_of(session, node, indoors)
    area = (
        await estate.space(session, constants, node)
        if inside
        else await estate.yard(session, constants, node)
    )
    needed = gear.mass_of(catalog, item.type_key, qty) / constants[R.BUILD_FLOOR_PER_M2]
    if needed > area["free"]:
        raise NoRoom(
            key="storage-no-room",
            inside="true" if inside else "false",
            free=area["free"],
            needed=needed,
        )

    yard = await world.node_container(session, node)
    put_down = await world.move_stack(session, item, yard, qty, outdoors=not inside)
    await events.record(
        session,
        EventKind.ITEM_DROPPED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        type_key=item.type_key,
        amount=put_down,
        roofed=inside,
    )
    return put_down


async def pick(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    quantity: float | None = None,
) -> float:
    """Pick up what lies here. Whoever got in may take it (D-204).

    The floor used to be closed to strangers, and that stood in for a door the
    location did not have. Now the door is real: the holder shuts entry and keeps
    lists (`engine/access.py`), and what lies inside is taken by anyone they let
    in. Locked up means behind a shut door or in a chest (D-181), not behind a
    rule saying "do not take".
    """

    if body.state is not BodyState.ALIVE:
        raise StorageError(key="storage-dead-picks")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise StorageError(key="storage-body-off-node")
    #: Either surface: what the hand reaches for is what it can see, and both
    #: lists are on one screen (D-244).
    yard = await world.node_container(session, node)
    if item.container_id != yard.id:
        raise StorageError(key="storage-not-on-ground")

    await _require_inside(session, node, body)

    #: A relic of the Forerunners is not picked up, ever (D-232): it was found
    #: here, and the world holds no second copy of it. The refusal is here
    #: rather than in the carry limit, which would only say "too heavy" and
    #: leave a wagon as the loophole.
    if catalog.recipes.is_relic(item.type_key):
        raise StorageError(key="storage-relic", goods=item.type_key)
    #: Built in place (D-268): the same door the relic is refused at -- a
    #: wagon, an exoskeleton and a market would otherwise carry off a furnace.
    if catalog.recipes.built(item.type_key):
        raise StorageError(key="storage-built-in-place", goods=item.type_key)
    #: What stands is taken up by `station.take`, and that door asks whose the
    #: place is (D-278); off the floor comes only what lies -- otherwise a guest
    #: would pocket the host's workbench past the owner's door.
    if item.installed:
        raise StorageError(key="storage-standing", goods=item.type_key)

    #: Fuel lying where a fuel plant stands is loaded, not stored (D-189):
    #: the station burns from this very container, so the pile IS its tank,
    #: and pouring in is a handover with no way back. Without this the
    #: promise was a docstring -- and once the works fund began paying for
    #: hauls (D-248), pour-collect-pick-up turned theft into a money pump.
    if (
        item.type_key in constants[R.ENERGY_FUEL_ENERGY]
        and await energy.plant_view(session, constants, node) is not None
    ):
        raise StorageError(key="storage-station-fuel", goods=item.type_key)

    qty = amount_float(item.amount) if quantity is None else quantity
    if qty <= 0:
        raise StorageError(key="storage-nothing-to-pick")
    await gear.check_carry(session, constants, catalog, body, item.type_key, qty)

    pocket = await world.body_container(session, body)
    taken = await world.move_stack(session, item, pocket, qty)
    await events.record(
        session,
        EventKind.ITEM_PICKED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        type_key=item.type_key,
        amount=taken,
    )
    return taken


async def hand(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    giver: Body,
    taker: Body,
    item: Item,
    quantity: float | None = None,
) -> float:
    """Hand a thing to somebody standing here.

    Matter moves physically, so this is in person on both sides: the giver is
    here and so is the taker, and there is no posting things over the Net. It is
    the shortest path between two people that does not go through the market,
    and the market is not always the right shape -- a gift, a wage in kind, a
    tool lent for an hour.

    The receiver's hands are not bottomless: the load limit is theirs to obey
    (D-146), so a full pair of hands refuses the parcel instead of swallowing it.
    """

    if giver.state is not BodyState.ALIVE:
        raise StorageError(key="storage-dead-hands")
    if taker.state is not BodyState.ALIVE:
        raise StorageError(key="storage-dead-receives")
    if giver.id == taker.id:
        raise StorageError(key="storage-self-hand")
    await travel.require_here(session, giver)
    #: Both in the same room: shouting across the map is not handing over.
    if taker.node_id != giver.node_id:
        raise StorageError(key="storage-person-not-here")

    pocket = await world.body_container(session, giver)
    if item.container_id != pocket.id:
        raise StorageError(key="storage-not-in-hands-to-hand")

    qty = amount_float(item.amount) if quantity is None else quantity
    if qty <= 0:
        raise StorageError(key="storage-nothing-to-hand")
    await gear.check_carry(session, constants, catalog, taker, item.type_key, qty)

    hands = await world.body_container(session, taker)
    given = await world.move_stack(session, item, hands, qty)
    await events.record(
        session,
        EventKind.ITEM_MOVED,
        actor_identity_id=giver.identity_id,
        node_id=giver.node_id,
        type_key=item.type_key,
        amount=given,
        to_identity_id=str(taker.identity_id),
        reason="handover",
    )
    return given


async def _allowed(session: AsyncSession, catalog: Catalog, body: Body, chest: Item) -> Node:
    """The common door of both actions: alive, here, entitled, and this is a storage."""

    if body.state is not BodyState.ALIVE:
        raise StorageError(key="storage-dead-moves")
    await travel.require_here(session, body)
    if not is_storage(catalog, chest.type_key):
        raise NotStorage(key="storage-not-a-storage", chest=chest.type_key)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body without a node is a bug
        raise StorageError(key="storage-body-off-node")
    yard = await world.node_container(session, node)
    if chest.container_id != yard.id:
        raise StorageError(key="storage-storage-not-here")
    if not await station.may_build(session, body, node):
        raise NotYours(key="storage-not-yours")
    return node
