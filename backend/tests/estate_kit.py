# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The estate tests' shared fixtures: a city with a printer, a funded buyer,
a house on a plot. Used by the estate files (`test_estate*.py`); not
collected by pytest.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog
from src.engine import city as town
from src.engine import ledger, world
from src.models.city import Citizen
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node, Surface
from src.units import money


async def _city(session: AsyncSession, catalog: Catalog):
    """A town: a core with the Forerunners' Printer and two plots at the first and second step."""
    from src.engine import travel

    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session,
        f"terra.town.{stamp}",
        "Городок",
        area_m2=1,
        layer=Layer.PLANET,
        parent=planet,
    )
    core = await world.create_node(
        session,
        f"terra.town.{stamp}.core",
        "Ядро",
        area_m2=100,
        parent=delegate,
        properties={"ring": 0, "precursors": True},
    )
    #: The bioprinter distance is measured from: the city centre (D-089).
    core_yard = await world.node_container(session, core)
    await world.grant_item(session, core_yard, world.BIOPRINTER, quality=60, origin="тест")

    near = await world.create_node(
        session,
        f"terra.town.{stamp}.lot1",
        "Ближний участок",
        area_m2=100,
        parent=delegate,
        properties={"plot": True},
    )
    far = await world.create_node(
        session,
        f"terra.town.{stamp}.lot2",
        "Дальний участок",
        area_m2=100,
        parent=delegate,
        properties={"plot": True},
    )
    await travel.connect(session, core, near, base_seconds=30, surface=Surface.PAVED)
    await travel.connect(session, near, far, base_seconds=30, surface=Surface.PAVED)

    city = await town.found(session, catalog, delegate, "Городок")
    for node in (core, near, far):
        node.owner_city_id = city.id
    await session.flush()
    return city, core, near, far


async def _buyer(
    session: AsyncSession,
    where: Node,
    *,
    funds: float = 1_000,
    city=None,
    citizen: bool = True,
):
    """The buyer. A citizen by default: land is sold to one's own (D-160)."""
    stamp = uuid.uuid4().hex[:6]
    identity = await world.create_identity(session, f"Покупатель-{stamp}")
    body = await world.print_body(session, identity, where)
    if citizen and city is not None:
        session.add(Citizen(identity_id=identity.id, city_id=city.id))
        await session.flush()
    if funds:
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=account.id,
            amount=money(funds),
            memo={},
        )
    return identity, body
