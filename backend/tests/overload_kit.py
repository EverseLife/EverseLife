# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Shared ground for the overload family: the two doors, and what they move.

Helpers and names only -- pytest does not collect this file, and no fixture
lives here: an import for the sake of a name in a signature is what ruff takes
away, and the fixture pytest then cannot find.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import world
from src.models.estate import Building
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node
from src.units import amount_float

SACK = "sack"
EXO = "exoskeleton"
BACKPACK = "simple_backpack"
#: Worn, and it neither raises the limit nor lightens the load.
SUIT = "insulated_suit"
STEEL = "steel"
ORE = "iron_ore"
INGOT = "iron_ingot"
NAILS = "nails"
FORGE = "forge"


async def _ground(session: AsyncSession):
    """Nobody's land under the open sky: what falls, falls on the ground."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.field.{stamp}", "Поле", area_m2=200)
    identity = await world.create_identity(session, f"Носильщик-{stamp}")
    body = await world.print_body(session, identity, node)
    return node, identity, body


async def _house(session: AsyncSession, area: float = 200):
    """Own plot with a roof: what falls, falls on the floor."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.home.{stamp}", "Дом", area_m2=200)
    node.owner_city_id = uuid.uuid4()
    session.add(Building(node_id=node.id, area_m2=area))
    await session.flush()
    identity = await world.create_identity(session, f"Хозяин-{stamp}")
    body = await world.print_body(session, identity, node)
    await world.grant_node(session, node, identity)
    return node, identity, body


async def _held(session: AsyncSession, body: Body, type_key: str) -> float:
    pocket = await world.body_container(session, body)
    total = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == pocket.id, Item.type_key == type_key
        )
    )
    return amount_float(int(total or 0))


async def _lying(session: AsyncSession, node: Node, type_key: str) -> float:
    yard = await world.node_container(session, node)
    total = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == yard.id, Item.type_key == type_key
        )
    )
    return amount_float(int(total or 0))


async def _hold(session: AsyncSession, body: Body, type_key: str, quantity: float) -> Item:
    """Put a thing straight into the hands, past every door: what the load is
    made of is not what this file is about."""
    pocket = await world.body_container(session, body)
    return await world.grant_item(
        session, pocket, type_key, amount=quantity, quality=60, origin="сценарий теста"
    )


async def _charged(session: AsyncSession, body: Body, charge: float = 50) -> Item:
    """A cell with charge in it: without one the frame is a frame, and lifts
    nothing (D-268). Every test below that leans on the exoskeleton's limit
    needs it, or the limit it measures is the bare thirty kilograms."""
    cell = await _hold(session, body, "battery", 1)
    cell.charge = Decimal(str(charge))
    cell.charged_at = datetime.now(UTC)
    await session.flush()
    return cell


async def _told(session: AsyncSession, identity_id: uuid.UUID, kind: EventKind) -> list[Event]:
    rows = await session.execute(
        select(Event).where(Event.kind == kind.value, Event.actor_identity_id == identity_id)
    )
    return list(rows.scalars().all())
