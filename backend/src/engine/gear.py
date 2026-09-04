# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Carried load: mass, limit and gear slots (D-146, D-129).

The carry limit was in the vault from the very start -- `inventory.carry_mass`,
"everything above -- only by vehicle" -- but items had no mass, and it meant
nothing. A player carried a thousand ore in the pocket, and the geography
everything was built for cost nothing.

## How it is computed

**Load** is the sum of masses of everything in the hands, including what is
worn -- an exoskeleton does not become weightless because it is put on. A
worn pack lightens the first kilograms it holds (`inventory.pack`, D-268);
the rest weighs what it weighs.

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
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
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


async def load_of(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body
) -> float:
    """How much the body carries now, kg. What is worn counts along with everything."""
    things = await world.contents(session, await world.body_container(session, body))
    from src.engine import storage  # noqa: PLC0415 -- lazy: storage -> gear (the carry limit)

    #: A full canister weighs its fill (D-230): the liquid is carried, it only
    #: lives one container deeper. One reading for all the vessels at once.
    own = sum(mass_of(catalog, thing.type_key, amount_float(thing.amount)) for thing in things)
    inside = await storage.contents_of(
        session, [t for t in things if storage.is_vessel(catalog, t.type_key)]
    )
    fill = sum(
        mass_of(catalog, thing.type_key, amount_float(thing.amount))
        for held in inside.values()
        for thing in held
    )
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
                    #: Worn means in these hands (D-305): a frame left on the
                    #: floor lifts nothing and so drinks nothing either.
                    Item.container_id == Container.id,
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
) -> None:
    """Whether this fits in the hands. Did not fit -- not taken, and that is not an error but
    weight."""
    bonus = mass_of(catalog, type_key, quantity)
    if bonus <= 0:
        return
    carries = await load_of(session, constants, catalog, body)
    limit = await capacity(session, constants, catalog, body)
    if carries + bonus > limit:
        raise Overloaded(key="gear-overloaded", carries=carries, limit=limit, extra=bonus)


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
