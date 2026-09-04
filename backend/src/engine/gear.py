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

**Worn means in the hands** (D-305). The slot names a thing, and a thing goes
where hands take it; a slot naming a pack that lies on the floor names
nothing. `is_worn` holds that rule for the whole engine -- the load, the
exoskeleton's lift, the suit that breathes (`oxygen.suited`) and the suit that
warms (`frost`) all ask it rather than the bare row -- and `require_off` is the
same rule said to a player: a worn thing comes off before it goes anywhere.

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
face -- it stays at the face until somebody comes for it, and with a machine
taken down off its stand: `station.take` leaves it lying, and the limit
answers at the pick-up (D-308).

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

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.engine import battery, events, stock, travel, world
from src.engine.errors import Refusal
from src.models.craft import BatchKind, BatchState, CraftBatch
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


class Worn(GearError):
    """A worn thing is not moved, sold or taken apart: it comes off first."""


class Unmade(GearError):
    """A thing already being taken apart is not put on: the work ends it."""


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


async def carried_mass(session: AsyncSession, catalog: Catalog, body: Body) -> float:
    """The matter in the hands, kg -- before a pack lightens any of it.

    What is worn counts along with everything else. This is the figure a pack
    is applied to, and the one in which kilograms may be added at all: the
    felt load is not additive, so two readings of it must never be summed.

    What those things hold inside counts too (D-313): a full canister weighs
    its fill, a full chest weighs what is stacked in it, a loaded barrow its
    load.
    """
    things = await world.contents(session, await world.body_container(session, body))
    own = sum(mass_of(catalog, thing.type_key, amount_float(thing.amount)) for thing in things)
    fill = await inner_mass(session, catalog, things)
    return own + fill


async def load_of(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body
) -> float:
    """How much the body carries now, kg. What is worn counts along with everything."""
    return packed(
        constants,
        catalog,
        await equipped(session, body),
        await carried_mass(session, catalog, body),
    )


def _pack_of(
    constants: Constants, catalog: Catalog, worn: dict[str, Item]
) -> dict[str, float] | None:
    """The worn pack, or nothing. One back, one pack -- the first found is it."""
    packs = constants[R.INVENTORY_PACK]
    for thing in worn.values():
        pack = packs.get(catalog.recipes.resolve(thing.type_key))
        if pack:
            return pack
    return None


def packed(constants: Constants, catalog: Catalog, worn: dict[str, Item], mass: float) -> float:
    """The load as the body feels it: the pack lightens what fits in it (D-268).

    A pack holds its first `capacity` kilograms of the load and multiplies
    them by its `factor`; packing is implied -- the first kilograms ride in
    it. The rest is carried as it is. The pack raises no limit: that is the
    exoskeleton's business, and the two never compete.

    The line is bent, not scaled: below the capacity a kilogram weighs
    `factor` of itself, above it a whole one. So the reading of a load is not
    the sum of the readings of its parts, and a question about a load that is
    about to change is a question about the whole of it -- `check_carry` and
    `matter_over` ask it in the two directions.
    """
    pack = _pack_of(constants, catalog, worn)
    if pack is None:
        return mass
    inside = min(mass, float(pack["capacity"]))
    return mass - inside * (1 - float(pack["factor"]))


def matter_over(
    constants: Constants, catalog: Catalog, worn: dict[str, Item], mass: float, limit: float
) -> float:
    """How much matter must leave a raw load of `mass` for it to fit `limit`, kg.

    `packed` read backwards, and it has to be read backwards rather than
    subtracted: with a pack the felt excess and the matter that removes it are
    different kilograms. While the load sits inside the pack's capacity, a
    kilogram out of the hands lightens the body by `factor` of itself, so
    putting down the felt excess would leave the body over the limit still.
    Past the capacity the two agree -- the only case the vault's packs have
    ever produced, and a tripwire in the tests says so the day one of them
    grows roomier than the limit.

    Matter, hence the name: `overload.shed` is the door that drops it (D-306),
    this is only the measure it drops by.
    """
    if packed(constants, catalog, worn, mass) <= limit:
        return 0.0
    pack = _pack_of(constants, catalog, worn)
    if pack is None:
        return mass - limit
    room, factor = float(pack["capacity"]), float(pack["factor"])
    #: Inside the pack the limit buys `limit / factor` kilograms of matter;
    #: past it the pack's whole discount is spent and the rest weighs itself.
    fits = limit / factor if factor > 0 and limit <= room * factor else limit + room * (1 - factor)
    return mass - fits


async def is_worn(session: AsyncSession, item: Item) -> bool:
    """Whether this thing is worn **right now** -- the rule, in one place.

    Worn is not a field on the thing and not a row in a table: it is a row and
    a place together. The slot record points at a thing, and a thing goes
    where hands take it -- onto the floor, into a chest, over a counter. The
    record knows nothing of that, and for a while nobody asked: a pack on the
    ground went on lightening the load, an exoskeleton in a hold went on
    lifting the limit, a suit sold at the terminal went on breathing for its
    former owner. So the question is put to the world: a thing is worn while
    it lies in the pocket of the body whose slot names it.

    A thing the vault gives no slot is answered without asking the database:
    ore and grain are never worn, and this is asked on every move in the world.

    **Not every reader of gear wants this.** `wear.daily_gear_wear` frays
    everything of the gear kind in the hands, worn or not, and does not ask
    here on purpose -- whether that matches its own "wears from wearing" is a
    question D-305 leaves open, not one to settle by wiring this in. What the
    slot *does* -- lift, lighten, breathe, warm -- is what asks.
    """

    if current_catalog().recipes.slot_of(item.type_key) is None:
        return False
    found = await session.scalar(
        select(Equipped.id)
        .join(Container, Container.owner_id == Equipped.body_id)
        .where(
            Equipped.item_id == item.id,
            Container.kind == ContainerKind.BODY,
            Container.id == item.container_id,
        )
        .limit(1)
    )
    return found is not None


async def require_off(session: AsyncSession, item: Item) -> None:
    """A worn thing does not leave the hands until it is taken off (D-305).

    Said in words rather than silently taken off: the slot is a choice, and
    the world does not undo a player's choice on their behalf.
    """
    if await is_worn(session, item):
        raise Worn(key="gear-worn-take-off-first", goods=item.type_key)


async def equipped(session: AsyncSession, body: Body) -> dict[str, Item]:
    """What is worn: slot -> thing. The same rule as `is_worn`, for a whole
    body at once: a slot naming a thing that is no longer in these hands names
    nothing."""
    pocket = await world.body_container(session, body)
    rows = (
        await session.execute(
            select(Equipped.slot, Item)
            .join(Item, Item.id == Equipped.item_id)
            .where(Equipped.body_id == body.id, Item.container_id == pocket.id)
        )
    ).all()
    return {slot: thing for slot, thing in rows}


async def capacity(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    worn: dict[str, Item] | None = None,
) -> float:
    """The carry limit with worn gear in mind, kg.

    An exoskeleton raises it (D-268) -- while a charged battery rides in the
    hands. Drained, it is a frame that weighs and lifts nothing.

    `worn` is for a caller that has already read it: the load and the limit
    are two questions about the same slots, and `look` asks both on every
    glance. Left out, they are read here.
    """
    if worn is None:
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

    Called by the tick for its own length. **A wearer with no charge left drops
    what the charge was carrying** (D-306): the frame stays on the shoulders
    and lifts nothing, so the limit is the bare pair of hands and everything
    above it lies down underfoot. One sweep answers every way the charge can
    go -- drunk to the bottom here, or the cell put down, given away, sold or
    spent as a recipe's input before the tick came round: whatever the road to
    it, the wearer is found without a charge and settles at the next tick.

    Returns the charge drunk, all wearers together.

    **The lock order is the bodies first, then the cells**, and both in id
    order. It is the order `overload._fall` takes when a frame comes off -- the
    body's row, then the stacks it moves -- so a tick draining a cell and a
    player undressing beside it never wait on each other. One query for every
    wearer's cells at once (`stock.locked_stacks` over all the pockets): a
    hand-over of a battery locks cells in id order too.
    """
    rate = constants[R.GEAR_EXO_ENERGY_PER_HOUR]
    if rate <= 0 or hours <= 0:
        return 0.0
    names = {catalog.recipes.resolve(name) for name in constants[R.INVENTORY_EXO_BONUS]}
    #: Keyed by body: the join gives one row per worn frame, and a body that
    #: ever wore two would otherwise be drunk from -- and shed -- twice.
    found = (
        await session.execute(
            select(Body.id, Container.id)
            .join(Container, Container.owner_id == Body.id)
            .join(Equipped, Equipped.body_id == Body.id)
            .join(Item, Item.id == Equipped.item_id)
            .where(
                Container.kind == ContainerKind.BODY,
                Item.type_key.in_(names),
                Body.state == BodyState.ALIVE,
                #: Worn means in these hands (D-315): a frame left on the floor
                #: lifts nothing and so drinks nothing either.
                Item.container_id == Container.id,
            )
        )
    ).all()
    if not found:
        return 0.0
    pocket_of = dict(found)

    #: Locked **and reread**: the rows below say where a fall lands
    #: (`body.node_id`) and are moved on, so a snapshot taken before the lock
    #: is the very thing the lock stands against (`stock.locked_stacks`).
    wearers = (
        (
            await session.execute(
                select(Body)
                .where(Body.id.in_(list(pocket_of)))
                .order_by(Body.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    pockets = sorted(set(pocket_of.values()))
    cells = await stock.locked_stacks(session, pockets, world.station_names(battery.BATTERY))
    by_pocket: dict[uuid.UUID, list[Item]] = {}
    for cell in cells:
        by_pocket.setdefault(cell.container_id, []).append(cell)
    drunk = 0.0
    for pocket in pockets:
        held = by_pocket.get(pocket)
        if held:
            drunk += await battery.drain_cells(session, constants, held, rate * hours, now=now)

    #: The frame lifts on a charge, so no charge is a fallen limit (D-306).
    #: Asked of the cells already read and locked above rather than of the
    #: database again: `battery.charged_carried` would walk the same pocket a
    #: second time, once per wearer, every minute of the world.
    for body in wearers:
        held = by_pocket.get(pocket_of[body.id], ())
        if any(battery.charge_of(constants, cell, now=now) > 0 for cell in held):
            continue
        await _settle(session, constants, catalog, body)
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

    Weighed as the load **will be**, not as it was plus raw kilograms: a pack
    that lightens the first kilograms lightens the ones about to arrive too
    (D-268). Adding the thing's own mass to a load already read through the
    pack charged full weight for what the pack was going to carry at
    `factor`, and refused a pickup the body could make.

    `inside` is what rides within the thing itself (D-313): a chest arrives
    with its contents, and weighing the lid alone would let a ton through a
    door that asked about four kilograms. Doors that mint goods by name --
    a harvest, a face, a poured litre -- have nothing inside and say nothing.
    """
    bonus = mass_of(catalog, type_key, quantity) + inside
    if bonus <= 0:
        return
    worn = await equipped(session, body)
    mass = await carried_mass(session, catalog, body)
    carries = packed(constants, catalog, worn, mass)
    after = packed(constants, catalog, worn, mass + bonus)
    limit = await capacity(session, constants, catalog, body, worn)
    if after > limit:
        #: The refusal names what the body would feel, not what the thing
        #: weighs on the ground: the three figures in the message have to add
        #: up for whoever reads it.
        raise Overloaded(key="gear-overloaded", carries=carries, limit=limit, extra=after - carries)


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
    #: A thing under the knife is not put on (D-305): recycling ends it, and
    #: the slot would empty itself when the batch finished. Repair is the
    #: opposite case and deliberately not here -- gear is mended without being
    #: taken off, and the batch works on the row where it lies.
    unmade = await session.scalar(
        select(CraftBatch.id)
        .where(
            CraftBatch.target_item_id == item.id,
            CraftBatch.kind == BatchKind.RECYCLE,
            CraftBatch.state != BatchState.DONE,
        )
        .limit(1)
    )
    if unmade is not None:
        raise Unmade(key="gear-taken-apart", goods=item.type_key)

    over = await _over(session, constants, catalog, body)
    previous_ = (
        await session.execute(
            select(Equipped).where(Equipped.body_id == body.id, Equipped.slot == slot)
        )
    ).scalar_one_or_none()
    taken_off: tuple[uuid.UUID, ...] = ()
    if previous_ is not None:
        if previous_.item_id == item.id:
            return slot
        taken_off = (previous_.item_id,)
        await session.delete(previous_)
        await session.flush()

    #: A slot row outlives the thing leaving the hands, and one row is all a
    #: thing gets: without this, a pack somebody wore and sold could never be
    #: worn again -- the buyer got a unique-key error instead of an answer.
    #: The thing is in these hands, so whoever the old row names is not
    #: wearing it (D-305), and the row is theirs no longer.
    stale = (
        await session.execute(select(Equipped).where(Equipped.item_id == item.id))
    ).scalar_one_or_none()
    if stale is not None:
        await session.delete(stale)
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
    #: Putting a thing on can **lower** the limit: one slot per thing, so a
    #: lighter frame takes the place of a heavier one and the previous came off
    #: above. What the old one lifted and the new one cannot falls (D-306).
    await _settle(session, constants, catalog, body, over, spare=taken_off)
    return slot


async def unequip(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body, slot: str
) -> Item | None:
    """Take off what is worn from a slot. The thing stays in the hands -- it was there anyway.

    **And what the frame was carrying does not.** The limit falls with the
    exoskeleton, and a load raised to its ceiling would otherwise stay in the
    pocket and walk out of the node: an overloaded body was said not to exist
    (D-265, D-268), and that was true only of the doors a thing comes in
    through. What no longer fits falls underfoot, heaviest first
    (`overload.shed`, D-306).
    """
    line = (
        await session.execute(
            select(Equipped).where(Equipped.body_id == body.id, Equipped.slot == slot)
        )
    ).scalar_one_or_none()
    if line is None:
        return None
    over = await _over(session, constants, catalog, body)
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
    await _settle(session, constants, catalog, body, over, spare=(line.item_id,))
    return thing


async def _over(session: AsyncSession, constants: Constants, catalog: Catalog, body: Body) -> float:
    """By how many kilograms the hands are over the limit right now."""
    return await load_of(session, constants, catalog, body) - await capacity(
        session, constants, catalog, body
    )


async def _settle(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    before: float | None = None,
    *,
    spare: tuple[uuid.UUID, ...] = (),
) -> float:
    """What no longer fits falls underfoot (D-265, D-306).

    `before` is the excess this act found, and dressing and undressing pass it:
    a frame taken off leaves more than it found and **the difference** lies
    down, while putting a suit on leaves it exactly as it was and touches
    nothing. An overload that was already there is not this door to answer for
    -- it arrived by another one, and that one settles it: a lost charge at the
    tick, which passes no `before` at all because losing the lift **is** the
    act, or an arrival past the limit where it lands (`overload.settle_load`).

    `spare` is what this act must not drop whatever it weighs: the thing just
    taken off stays in the hands (D-306), or "take the frame off" would end
    with the frame on the ground and out of reach of the hands that dropped it.
    """
    from src.engine import overload  # noqa: PLC0415 -- lazy: breaks overload -> gear (the limit)

    floor = 0.0
    if before is not None:
        if await _over(session, constants, catalog, body) <= before + overload.DUST:
            return 0.0
        floor = max(before, 0.0)
    return await overload.shed(session, constants, catalog, body, floor=floor, spare=spare)
