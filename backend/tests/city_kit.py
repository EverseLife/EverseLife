# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The city tests' shared fixtures: a capital with funds and a resident in it.
Used by the city files (`test_city*.py`); not collected by pytest.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog
from src.engine import city as town
from src.engine import ledger, world
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer
from src.units import money


async def _capital(session: AsyncSession, catalog: Catalog, *, funds: float = 0):
    """A city with a delegate node, built-up area and a founder."""
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session,
        f"terra.city.{stamp}",
        "Столица",
        area_m2=1,
        layer=Layer.PLANET,
        parent=planet,
    )
    core = await world.create_node(
        session,
        f"terra.city.{stamp}.core",
        "Ядро",
        area_m2=100,
        parent=delegate,
        properties={"ring": 0},
    )
    city = await town.found(session, catalog, delegate, "Столица")
    core.owner_city_id = city.id
    await session.flush()
    #: Governing is in-person (D-155): decisions are made where the
    #: "Administration" stands. In the starting world that is a separate node, in the test -- the
    #: core.
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, town.HALL, quality=65, origin="тест")

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
    return city, core


async def _resident(session: AsyncSession, node, name: str):
    identity = await world.create_identity(session, f"{name}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    return identity, body
