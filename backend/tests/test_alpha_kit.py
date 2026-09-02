# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the alpha tests share: a body, a master at a bench, a city yard with a
pool, and what a body carries. Not collected by pytest -- helpers only, no
fixture lives here (CLAUDE.md, the `<family>_kit.py` rule).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import world
from src.models.estate import Building
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Layer

ORE = "iron_ore"
BENCH = "workbench"
MAKE = "handle"
WOOD = "wood"
#: A liquid and something to keep it in: liquids live only in vessels (D-230).
FUEL = "rocket_fuel"
CANISTER = "canister"


async def _body(session: AsyncSession) -> Body:
    """A body standing on a planet: a survey looks for a place on one, so the
    node needs a parent even where the test only cares about the term."""
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    here = await world.create_node(
        session, f"terra.here.{stamp}", "Здесь", area_m2=100, layer=Layer.PLANET, parent=planet
    )
    identity = await world.create_identity(session, f"Тэрн-{stamp}")
    return await world.print_body(session, identity, here)


async def _body(session: AsyncSession) -> Body:
    """A body standing on a planet: a survey looks for a place on one, so the
    node needs a parent even where the test only cares about the term."""
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    here = await world.create_node(
        session, f"terra.here.{stamp}", "Здесь", area_m2=100, layer=Layer.PLANET, parent=planet
    )
    identity = await world.create_identity(session, f"Тэрн-{stamp}")
    return await world.print_body(session, identity, here)


async def _master(session: AsyncSession):
    """A workshop with a bench, and a master who knows what to make on it."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.shop.{stamp}", "Двор", area_m2=200)
    session.add(Building(node_id=node.id, area_m2=200))
    await session.flush()
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, BENCH, quality=60, origin="сценарий теста")
    identity = await world.create_identity(session, f"Мастер-{stamp}")
    body = await world.print_body(session, identity, node)
    await world.learn(session, identity, MAKE)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, WOOD, amount=50, quality=60, origin="сценарий теста")
    return node, body


async def _carried(session: AsyncSession, body: Body) -> list[Item]:
    where = await world.body_container(session, body)
    return list(
        (await session.execute(select(Item).where(Item.container_id == where.id))).scalars()
    )


async def _grid(session: AsyncSession):
    """A city yard with a pool, and a body standing in it."""
    stamp = uuid.uuid4().hex[:8]
    capital = await world.create_node(
        session, f"terra.grid.{stamp}", "Столица", area_m2=1, layer=Layer.PLANET
    )
    yard = await world.create_node(
        session, f"terra.grid.{stamp}.yard", "Двор", area_m2=200, layer=Layer.CITY, parent=capital
    )
    identity = await world.create_identity(session, f"Горожанин-{stamp}")
    return yard, await world.print_body(session, identity, yard)
