# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What a hull breathes and a body carries: the vessels on the life support's
line, the reserve, the suit and the cylinders on the belt.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import ship as vessels
from src.engine import world
from src.engine.oxygen._base import SUIT
from src.engine.ship import lines
from src.models.gear import Equipped
from src.models.identity import Body
from src.models.inventory import Container, ContainerKind, Item
from src.models.ship import Ship
from src.units import (
    amount_float,
)

# --- what a hull holds ---------------------------------------------------------


async def systems_of(
    session: AsyncSession, ship: Ship, *, things: list[Item] | None = None
) -> list[Item]:
    """The life support systems standing aboard, in id order: what the air line hangs on."""
    hold = things if things is not None else await lines.hold_of(session, ship)
    names = world.station_names(vessels.LIFE_SUPPORT)
    return sorted(
        (one for one in hold if one.installed and one.type_key in names),
        key=lambda one: one.id,
    )


async def breathable_stacks(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    things: list[Item] | None = None,
) -> list[Item]:
    """The oxygen the crew can reach: what lies in the vessels on the life
    support's line (D-288).

    The system is the one thing aboard that breathes for people, and it
    drinks where its lines say -- any vessel installed aboard by default, the
    ones the owner named when a line is drawn. A cylinder in the hands, on the
    floor or packed in a chest is luggage: nothing aboard breathes it, and the
    word for that is on the thing itself (`installed`). No system at all --
    nothing is breathable, however much oxygen stands in the hold, and that is
    the refusal casting off exists for (`flight._leaving`).

    `things` is a reading of the hold when the caller already has it.
    """
    hold = things if things is not None else await lines.hold_of(session, ship)
    systems = await systems_of(session, ship, things=hold)
    if not systems:
        return []
    return await lines.stacks_for(session, catalog, ship, systems, lines.air_port(), things=hold)


async def reserve(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    things: list[Item] | None = None,
) -> float:
    """Oxygen the crew can actually breathe: what stands on the life support's line."""
    stacks = await breathable_stacks(session, constants, catalog, ship, things=things)
    return sum(amount_float(stack.amount) for stack in stacks)


def hull_draw(constants: Constants, crew: int) -> float:
    """What a crew of this size breathes an hour aboard."""
    return crew * constants[R.OXYGEN_CREW_DRAW]


# --- what a body carries -------------------------------------------------------


async def suited(session: AsyncSession, catalog: Catalog, body: Body) -> bool:
    """Whether a suit is worn. Not carried -- worn: the suit is the connection."""
    worn = (
        (
            await session.execute(
                select(Item)
                .join(Equipped, Equipped.item_id == Item.id)
                .where(Equipped.body_id == body.id)
            )
        )
        .scalars()
        .all()
    )
    suits = world.station_names(SUIT)
    return any(catalog.recipes.resolve(thing.type_key) in suits for thing in worn)


async def cylinders(session: AsyncSession, body: Body) -> list[Item]:
    """The oxygen a body can actually breathe: what lies inside vessels in its hands.

    Inside, not among: a liquid exists only in a vessel (D-230), so this is the
    stacks of air in the storages of the things in the pocket -- and a vessel
    standing in the node is somebody's property of the place, not this body's
    breath.
    """
    pocket = await world.body_container(session, body)
    carried_ = select(Item.id).where(Item.container_id == pocket.id)
    insides = select(Container.id).where(
        Container.kind == ContainerKind.STORAGE, Container.owner_id.in_(carried_)
    )
    rows = await session.execute(
        select(Item)
        .where(Item.container_id.in_(insides), Item.type_key == vessels.AIR)
        .order_by(Item.id)
    )
    return list(rows.scalars().all())


async def carried(session: AsyncSession, body: Body) -> float:
    return sum(amount_float(stack.amount) for stack in await cylinders(session, body))
