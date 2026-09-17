# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The works tests' shared ground: a fund topped up, a city with a ruler and a
worn civic plot, and a worker standing where the work is (D-248).

Used by the `test_works*.py` family; not collected by pytest. No real fixture
lives here on purpose -- a `@pytest.fixture` in a kit is imported for its name
alone, ruff removes the import as unused, and pytest then cannot find it.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import city as town
from src.engine import ledger, works, world
from src.engine.estate.building import kinds
from src.models.estate import Building
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node
from src.units import money


async def _feed_fund(session: AsyncSession, amount: int) -> None:
    """Top the fund up directly: the recycling path has tests of its own."""
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.WORKS_PRINT,
        debit=genesis.id,
        credit=(await works.fund_account(session)).id,
        amount=amount,
    )


async def _city_with_ruler(session: AsyncSession, catalog: Catalog, *, funds: float = 0):
    """A city, its core with the administration, and a ruler standing in it."""
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session,
        f"terra.city.{stamp}",
        f"Город-{stamp}",
        area_m2=1,
        layer=Layer.PLANET,
        parent=planet,
    )
    core = await world.create_node(
        session, f"terra.city.{stamp}.core", "Ядро", area_m2=100, parent=delegate
    )
    city = await town.found(session, catalog, delegate, f"Город-{stamp}")
    core.owner_city_id = city.id
    await session.flush()
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, town.HALL, quality=65, origin="тест")

    ruler = await world.create_identity(session, f"Мэр-{stamp}")
    ruler_body = await world.print_body(session, ruler, core)
    await town.install_founder(session, city, ruler)

    if funds:
        treasury = await town.treasury(session, city)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=treasury.id,
            amount=money(funds),
        )
    return city, core, ruler, ruler_body


async def _civic_plot(
    session: AsyncSession, constants: Constants, city, core, *, condition: float
) -> Node:
    """A city plot next door with one worn house on it."""
    plot = await world.create_node(
        session,
        f"{core.key}.plot{uuid.uuid4().hex[:4]}",
        "Городской двор",
        area_m2=100,
        parent=core,
    )
    plot.owner_city_id = city.id
    session.add(
        Building(
            node_id=plot.id,
            area_m2=20,
            footprint_m2=20,
            floors=1,
            kind=kinds(constants)[0],
            condition=condition,
        )
    )
    await session.flush()
    return plot


async def _worker_at(session: AsyncSession, node: Node, *, materials: dict | None = None):
    identity = await world.create_identity(session, f"Работник-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    if materials:
        pocket = await world.body_container(session, body)
        for name, qty in materials.items():
            await world.grant_item(session, pocket, name, amount=qty, origin="тест")
    return identity, body
