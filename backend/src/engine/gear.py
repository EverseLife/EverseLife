# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Carried load: mass, limit and gear slots (D-146, D-129).

The carry limit was in the vault from the very start -- `inventory.carry_mass`,
"everything above -- only by vehicle" -- but items had no mass, and it meant
nothing. A player carried a thousand ore in the pocket, and the geography
everything was built for cost nothing.

## How it is computed

**Load** is the sum of masses of everything in the hands, including what is
worn -- an exoskeleton does not become weightless because it is put on -- and
including what those things hold inside (D-313): a full canister weighs its
fill, a full chest weighs what is stacked in it. A worn pack lightens the
first kilograms it holds (`inventory.pack`, D-268); the rest weighs what it
weighs.

**Limit** is `inventory.carry_mass` plus `inventory.exo_bonus` for a worn
exoskeleton -- while a charged battery rides in the hands (D-268). A pack
raises nothing; clothes and armour take the slot but add nothing to carry --
their effect arrives with environment and combat.

**One slot per thing.** Without slots a player would wear three backpacks and
the limit would cease to exist; the slot is the constraint itself, not an
interface decoration.

## Where the limit is checked

Where the player **takes a thing in hand**: purchase from the terminal,
harvest, emptying a hopper. This is not an error message but the reason
wagons, caravans and the carter's profession exist.

Two doors, because the question has two shapes. `check_carry` asks about
**goods by name** -- a harvest, a poured litre, an hour at the face: matter
that arrives without a row of its own and holds nothing. `check_carry_thing`
asks about a **row that moves whole** -- off the floor, out of a chest, out
of a hold, from another's hands -- and that one weighs what is inside it
(D-313). A door that moves a thing and asks the first question has a hole
the size of the thing's contents.

What is made at a machine does not fall under the limit: it lies where it was
made and becomes a load only when taken. Likewise with what is mined at the
face -- it stays at the face until somebody comes for it.

## What is not here yet

* **Volume.** `inventory.carry_volume` exists in the vault, items have no
  volume. Creating it in code would mean inventing data that does not exist (D-065);
* **Transport.** It is the answer to the limit (D-107) and arrives with its
  own mechanic: cargo finally has mass, and `transport.mass_*` were waiting for it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.engine import battery, events, stock, travel, world
from src.engine.errors import Refusal
from src.models.event import EventKind
from src.models.gear import Equipped
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.units import amount_float


class GearError(Refusal):
    pass


class NotGear(GearError):
    """This thing is not worn: an item's slot comes from vault data."""


class Overloaded(GearError):
    """No more than the limit is taken in hand. Everything above -- only by vehicle."""


def mass_of(catalog: Catalog, type_key: str, quantity: float) -> float:
    """The mass of this much of this item, kg."""
    return catalog.recipes.mass_of(type_key) * quantity


#: Containers a **thing** owns, and which therefore travel with it: a
#: storage's inside (D-181) and a vehicle's hold (D-157). The market's cells
#: belong to an identity and a pocket to a body, so neither is ever carried.
INSIDE_KINDS = (ContainerKind.STORAGE, ContainerKind.VEHICLE)


def has_store(catalog: Catalog, type_key: str) -> bool:
    """Whether the vault gives this thing capacity (`store`, D-181).

    The primitive `storage.is_storage` is built on; it lives here because the
    carry limit is below both `storage` and `transport` in the import order
    and cannot call up to either.
    """
    try:
        return bool(catalog.recipes.recipe(type_key).store)
    except Exception:  # noqa: BLE001 -- raw material has no recipe, and that is normal
        return False


def is_vehicle_kind(catalog: Catalog, type_key: str) -> bool:
    """Whether the vault calls this a vehicle (`kind: vehicle`, D-090, D-157).

    The primitive `transport.is_vehicle` is built on -- see `has_store`.
    """
    try:
        return catalog.recipes.recipe(type_key).kind is ItemKind.VEHICLE
    except Exception:  # noqa: BLE001 -- raw material has no recipe, and that is normal
        return False


def holds_things(catalog: Catalog, type_key: str) -> bool:
    """Whether a thing of this kind can have anything inside it: a storage has
    capacity, a vehicle has a hold. One question, so no door forgets a half."""
    return has_store(catalog, type_key) or is_vehicle_kind(catalog, type_key)


async def inner_mass(session: AsyncSession, catalog: Catalog, things: Sequence[Item]) -> float:
    """The mass riding inside the things themselves, kg (D-313).

    A full canister weighs its fill (D-230), and so do a full chest and a
    loaded barrow: what is carried is carried, it only lives a container
    deeper, and a lid is not a way out of the limit. Answered for a whole list
    at once -- the inventory asks it of every hand at every `look`, and the
    carry limit at every pick-up.

    Containers nest -- a chest into a chest, a chest into a barrow -- so the
    reading walks down until a layer holds nothing. Each layer is two queries,
    not two per thing; hands holding no container at all cost none; and only
    the three columns the mass needs are read, because a chest of two hundred
    stacks would otherwise be hydrated whole at every `look`.

    Containers visited are remembered. The doors cannot build a cycle -- to be
    put into a box a thing must be in the hands, and a box that holds it lies
    in a node -- but a walk that trusts that would hang a request inside a
    transaction if one ever appeared, and the answer to "how would it get
    there" must not be the only thing standing between a read and a hang.
    """
    total = 0.0
    layer = [
        (thing.id, thing.type_key) for thing in things if holds_things(catalog, thing.type_key)
    ]
    seen: set[uuid.UUID] = set()
    while layer:
        holds = [
            hold
            for hold in (
                (
                    await session.execute(
                        select(Container.id).where(
                            Container.kind.in_(INSIDE_KINDS),
                            Container.owner_id.in_([owner for owner, _ in layer]),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if hold not in seen
        ]
        if not holds:
            break
        seen.update(holds)
        rows = (
            await session.execute(
                select(Item.id, Item.type_key, Item.amount).where(Item.container_id.in_(holds))
            )
        ).all()
        total += sum(mass_of(catalog, key, amount_float(amount)) for _, key, amount in rows)
        layer = [(found, key) for found, key, _ in rows if holds_things(catalog, key)]
    return total


async def load_of(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body
) -> float:
    """How much the body carries now, kg. What is worn counts along with everything."""
    things = await world.contents(session, await world.body_container(session, body))
    own = sum(mass_of(catalog, thing.type_key, amount_float(thing.amount)) for thing in things)
    fill = await inner_mass(session, catalog, things)
    return packed(constants, catalog, await equipped(session, body), own + fill)


def packed(constants: Constants, catalog: Catalog, worn: dict[str, Item], mass: float) -> float:
    """The load as the body feels it: the pack lightens what fits in it (D-268).

    A pack holds its first `capacity` kilograms of the load and multiplies
    them by its `factor`; packing is implied -- the first kilograms ride in
    it. The rest is carried as it is. The pack raises no limit: that is the
    exoskeleton's business, and the two never compete.
    """
    packs = constants[R.INVENTORY_PACK]
    for thing in worn.values():
        pack = packs.get(catalog.recipes.resolve(thing.type_key))
        if pack:
            inside = min(mass, float(pack["capacity"]))
            return mass - inside * (1 - float(pack["factor"]))
    return mass


async def equipped(session: AsyncSession, body: Body) -> dict[str, Item]:
    """What is worn: slot -> thing."""
    lines = (
        (await session.execute(select(Equipped).where(Equipped.body_id == body.id))).scalars().all()
    )
    result: dict[str, Item] = {}
    for line in lines:
        thing = await session.get(Item, line.item_id)
        if thing is not None:
            result[line.slot] = thing
    return result


async def capacity(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body
) -> float:
    """The carry limit with worn gear in mind, kg.

    An exoskeleton raises it (D-268) -- while a charged battery rides in the
    hands. Drained, it is a frame that weighs and lifts nothing.
    """
    worn = await equipped(session, body)
    lift = exo_bonus(constants, catalog, worn)
    if lift > 0 and not await battery.charged_carried(session, constants, body):
        lift = 0.0
    return constants[R.INVENTORY_CARRY_MASS] + lift


def exo_bonus(constants: Constants, catalog: Catalog, worn: dict[str, Item]) -> float:
    """What the worn exoskeleton would add, kg -- powered or not."""
    bonuses = constants[R.INVENTORY_EXO_BONUS]
    return sum(bonuses.get(catalog.recipes.resolve(thing.type_key), 0.0) for thing in worn.values())


async def wear_exoskeletons(
    session: AsyncSession, constants: Constants, catalog: Catalog, *, hours: float, now: datetime
) -> float:
    """Every worn exoskeleton drinks from the batteries in its wearer's hands (D-268).

    Called by the tick for its own length. A wearer with no charge left keeps
    the frame on and lifts nothing: the limit falls, and the doors that read it
    refuse the next pickup. Nothing falls out of the hands by itself.

    One query and one lock order for every wearer's cells at once
    (`stock.locked_stacks` over all the pockets): a hand-over of a battery
    locks cells in id order too, and two orders would be a deadlock.
    Returns the charge drunk, all wearers together.
    """
    rate = constants[R.GEAR_EXO_ENERGY_PER_HOUR]
    if rate <= 0 or hours <= 0:
        return 0.0
    names = {catalog.recipes.resolve(name) for name in constants[R.INVENTORY_EXO_BONUS]}
    pockets = (
        (
            await session.execute(
                select(Container.id)
                .join(Body, Body.id == Container.owner_id)
                .join(Equipped, Equipped.body_id == Body.id)
                .join(Item, Item.id == Equipped.item_id)
                .where(
                    Container.kind == ContainerKind.BODY,
                    Item.type_key.in_(names),
                    Body.state == BodyState.ALIVE,
                )
                .order_by(Container.id)
            )
        )
        .scalars()
        .all()
    )
    if not pockets:
        return 0.0
    cells = await stock.locked_stacks(session, pockets, world.station_names(battery.BATTERY))
    by_pocket: dict[uuid.UUID, list[Item]] = {}
    for cell in cells:
        by_pocket.setdefault(cell.container_id, []).append(cell)
    drunk = 0.0
    for pocket in pockets:
        held = by_pocket.get(pocket)
        if held:
            drunk += await battery.drain_cells(session, constants, held, rate * hours, now=now)
    return drunk


async def check_carry(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    type_key: str,
    quantity: float,
    *,
    inside: float = 0.0,
) -> None:
    """Whether this fits in the hands. Did not fit -- not taken, and that is not an error but
    weight.

    `inside` is what rides within the thing itself (D-313): a chest arrives
    with its contents, and weighing the lid alone would let a ton through a
    door that asked about four kilograms. Doors that mint goods by name --
    a harvest, a face, a poured litre -- have nothing inside and say nothing.
    """
    bonus = mass_of(catalog, type_key, quantity) + inside
    if bonus <= 0:
        return
    carries = await load_of(session, constants, catalog, body)
    limit = await capacity(session, constants, catalog, body)
    if carries + bonus > limit:
        raise Overloaded(key="gear-overloaded", carries=carries, limit=limit, extra=bonus)


async def moved_inside(
    session: AsyncSession, catalog: Catalog, item: Item, quantity: float
) -> float:
    """What rides inside a thing when it moves, kg (D-313).

    Only a whole row brings its contents along: splitting a stack hands over a
    bare thing, and what was inside stays with the row that keeps it. Every
    door into something bounded by mass -- the hands, a chest, a hold -- adds
    this to what it weighs, or it weighs the lid and lets the load through.
    """
    if quantity < amount_float(item.amount):
        return 0.0
    return await inner_mass(session, catalog, [item])


async def check_carry_thing(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    quantity: float,
) -> None:
    """Whether this **thing** fits in the hands, contents and all (D-313).

    The door for a row that moves whole -- off the floor, out of a chest, out
    of a hold, from another's hands.

    The row is taken for the transaction first, and that is the point rather
    than a precaution: what is inside it is read here and moved a moment
    later, and between the two a second session may fill it. Read a chest
    holding five kilograms, have three hundred put in, walk off with all of
    it -- the very hole this door exists to shut, one window over. `put`
    takes the same row, so the two serialise on it (CLAUDE.md, the remainder
    rule); `world.move_stack` takes it again below, which costs nothing.
    """
    await session.execute(select(Item.id).where(Item.id == item.id).with_for_update())
    inside = await moved_inside(session, catalog, item, quantity)
    await check_carry(session, constants, catalog, body, item.type_key, quantity, inside=inside)


async def equip(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    *,
    now: datetime | None = None,
) -> str:
    """Wear a thing. Slot taken -- the previous one comes off by itself.

    In-person only in the sense that the thing must be in the hands: a
    backpack lying in another city cannot be put on.
    """
    if body.state is not BodyState.ALIVE:
        raise GearError(key="gear-dead-dresses")
    await travel.require_here(session, body)

    slot = catalog.recipes.slot_of(item.type_key)
    if slot is None:
        raise NotGear(key="gear-no-slot", goods=item.type_key)
    if slot not in catalog.recipes.gear_slots:  # pragma: no cover -- vault data
        #: The slot id travels raw: slots have no `NAME()` entry, and this
        #: refusal is about the vault's data, not about a thing in the world.
        raise NotGear(key="gear-unknown-slot", slot=slot)

    pocket = await world.body_container(session, body)
    if item.container_id != pocket.id:
        raise GearError(key="gear-not-in-hands")

    previous_ = (
        await session.execute(
            select(Equipped).where(Equipped.body_id == body.id, Equipped.slot == slot)
        )
    ).scalar_one_or_none()
    if previous_ is not None:
        if previous_.item_id == item.id:
            return slot
        await session.delete(previous_)
        await session.flush()

    session.add(Equipped(body_id=body.id, slot=slot, item_id=item.id))
    await session.flush()
    await events.record(
        session,
        EventKind.GEAR_EQUIPPED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(item.id),
        type_key=item.type_key,
        slot=slot,
    )
    return slot


async def unequip(session: AsyncSession, body: Body, slot: str) -> Item | None:
    """Take off what is worn from a slot. The thing stays in the hands -- it was there anyway."""
    line = (
        await session.execute(
            select(Equipped).where(Equipped.body_id == body.id, Equipped.slot == slot)
        )
    ).scalar_one_or_none()
    if line is None:
        return None
    thing = await session.get(Item, line.item_id)
    await session.delete(line)
    await session.flush()
    await events.record(
        session,
        EventKind.GEAR_UNEQUIPPED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(line.item_id),
        slot=slot,
    )
    return thing


async def drop_missing(session: AsyncSession, item_id: uuid.UUID) -> None:
    """Remove the worn record if the thing is gone.

    A thing may run out by wear or go to the market -- the slot must not
    remember what does not exist.
    """

    line = (
        await session.execute(select(Equipped).where(Equipped.item_id == item_id))
    ).scalar_one_or_none()
    if line is not None:
        await session.delete(line)
        await session.flush()
