# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The worn rule (D-305): a thing is worn while it lies in the pocket of the
body whose slot names it -- asked of one thing, said to a player, and read
for a whole body at once.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import current_catalog
from src.engine import world
from src.engine.gear._base import Worn
from src.models.gear import Equipped
from src.models.identity import Body
from src.models.inventory import Container, ContainerKind, Item


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
