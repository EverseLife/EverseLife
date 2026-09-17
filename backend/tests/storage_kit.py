# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the tests of a spent storage build a workshop out of.

A bench where a battery is made -- lead, sulphuric acid and a clay pot, the one
recipe of the vault that spends a vessel -- a vessel with water in it, and the
question the defect was about: goods lying inside a storage that is no longer
there. Shared by `test_spent_storage.py` and `test_races_storage.py`, which is
why it is here and not beside one of them (the family's own pattern, see
`mining_kit.py`).

Pytest does not collect this file: it holds no tests and no fixtures -- a
real `@pytest.fixture` must not live here, because the import that puts its
name in a signature reads as unused to ruff and pytest never finds it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.engine import storage, world
from src.models.inventory import INSIDE_KINDS, Container, ContainerKind, Item
from src.units import amount_float

BATTERY = "battery"
POT = "clay_pot"
CANISTER = "canister"
BARROW = "wheelbarrow"
LEAD = "lead"
ACID = "sulfuric_acid"
WATER = "water"
BENCH = "workbench"


async def _bench(session: AsyncSession):
    """A workbench on unowned ground and a master who knows the battery.

    Unowned, so the yard is within the master's reach (D-315): the vessels of
    a test lie on it as well as in the hands.
    """
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.pots.{stamp}", "Workshop", area_m2=100)
    identity = await world.create_identity(session, f"Master-{stamp}")
    body = await world.print_body(session, identity, node)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, BENCH, quality=60, origin="test")
    await world.learn(session, identity, BATTERY)
    return node, identity, body, yard


async def _vessel(
    session: AsyncSession,
    container: Container,
    type_key: str,
    *,
    water: float,
    quality: float = 60,
) -> Item:
    """A vessel lying in a container, with this much water in it.

    Its inside is made even when it stays empty: a pot poured out keeps its
    container, and an empty container is not "something inside".
    """
    vessel = await world.grant_item(
        session, container, type_key, quality=quality, origin="test", installed=False
    )
    inside = await storage.inside(session, vessel)
    if water > 0:
        await world.grant_item(session, inside, WATER, amount=water, origin="test")
    return vessel


async def _water_in(session: AsyncSession, vessel_id: uuid.UUID) -> float:
    """What the vessel holds, summed in the database rather than off rows the
    session remembers: whether the vessel is still there is `_there`'s question."""
    rows = await session.execute(
        select(Item.amount)
        .join(Container, Item.container_id == Container.id)
        .where(Container.kind == ContainerKind.STORAGE, Container.owner_id == vessel_id)
    )
    return sum(amount_float(value) for value in rows.scalars().all())


async def _orphaned(session: AsyncSession) -> int:
    """Stacks lying inside a storage or a hold that no longer exists -- matter
    that left the world without a word, which is what a spent vessel used to
    leave."""
    owner = aliased(Item)
    return int(
        await session.scalar(
            select(func.count(Item.id))
            .join(Container, Item.container_id == Container.id)
            .where(
                Container.kind.in_(INSIDE_KINDS),
                ~exists().where(owner.id == Container.owner_id),
            )
        )
        or 0
    )


async def _there(session: AsyncSession, item_id: uuid.UUID) -> bool:
    found = await session.scalar(select(Item.id).where(Item.id == item_id))
    return found is not None
