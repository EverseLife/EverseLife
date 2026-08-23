# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Carried load: mass, limit and gear slots (D-146, D-129).

The carry limit was in the vault from the very start -- `inventory.carry_mass`,
"everything above -- only by vehicle" -- but items had no mass, and it meant
nothing. A player carried a thousand ore in the pocket, and the geography
everything was built for cost nothing.

## How it is computed

**Load** is the sum of masses of everything in the hands, including what is
worn: an exoskeleton does not become weightless because it is put on.

**Limit** is `inventory.carry_mass` plus `inventory.carry_bonus` per worn
thing. A backpack and an exoskeleton raise it, clothes and armour take the
slot but add nothing to carry -- their effect arrives with environment and combat.

**One slot per thing.** Without slots a player would wear three backpacks and
the limit would cease to exist; the slot is the constraint itself, not an
interface decoration.

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

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, travel, world
from src.engine.errors import Refusal
from src.models.event import EventKind
from src.models.gear import Equipped
from src.models.identity import Body, BodyState
from src.models.inventory import Item
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


async def load_of(session: AsyncSession, catalog: Catalog, body: Body) -> float:
    """How much the body carries now, kg. What is worn counts along with everything."""
    things = await world.contents(session, await world.body_container(session, body))
    return sum(mass_of(catalog, thing.type_key, amount_float(thing.amount)) for thing in things)


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
    """The carry limit with worn gear in mind, kg."""
    bonuses = constants[R.INVENTORY_CARRY_BONUS]
    worn = await equipped(session, body)
    increment = sum(
        bonuses.get(catalog.recipes.resolve(thing.type_key), 0.0) for thing in worn.values()
    )
    return constants[R.INVENTORY_CARRY_MASS] + increment


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
    carries = await load_of(session, catalog, body)
    limit = await capacity(session, constants, catalog, body)
    if carries + bonus > limit:
        raise Overloaded(
            f"не унести: в руках {carries:.1f} кг из {limit:.0f}, "
            f"а это ещё {bonus:.1f} кг. Всё сверх — только транспортом"
        )


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
        raise GearError("мёртвое тело не одевается")
    await travel.require_here(session, body)

    slot = catalog.recipes.slot_of(item.type_key)
    if slot is None:
        raise NotGear(f"{item.type_key!r} не надевается: у него нет слота")
    if slot not in catalog.recipes.gear_slots:  # pragma: no cover -- vault data
        raise NotGear(f"слота {slot!r} в мире нет")

    pocket = await world.body_container(session, body)
    if item.container_id != pocket.id:
        raise GearError("вещь не в руках: надевают своё")

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
