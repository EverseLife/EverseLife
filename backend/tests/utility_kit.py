# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The meter tests' shared ground: a city with a grid, a plot in it, a charged
pool and a resident with money (D-135, D-149).

Used by `test_utility.py` and `test_races_meter.py`; not collected by pytest.
No real fixture lives here on purpose -- a `@pytest.fixture` in a kit is
imported for its name alone, ruff removes the import as unused, and pytest
then cannot find it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import energy, ledger, world
from src.models.ledger import AccountKind, PostingReason
from src.models.world import PLOT, Layer
from src.units import money


async def _city(session: AsyncSession, catalog: Catalog, name: str = "Столица"):
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
    #: A plot, and marked as one: the door belongs to a plot the authority
    #: hands out, not to every node a city owns (D-199, D-282).
    home = await world.create_node(
        session,
        f"terra.city.{stamp}.home",
        "Дом",
        area_m2=100,
        parent=delegate,
        properties={PLOT: True},
    )
    city = await town.found(session, catalog, delegate, name)
    home.owner_city_id = city.id
    await session.flush()
    return city, delegate, home


async def _pool(session: AsyncSession, constants: Constants, node, qty: float):
    pool = await energy.pool_of(session, constants, node)
    assert pool is not None
    pool.stored = Decimal(str(qty))
    await session.flush()
    return pool


async def _resident(session: AsyncSession, node, name: str, *, funds: float = 0):
    identity = await world.create_identity(session, f"{name}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    if funds:
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=account.id,
            amount=money(funds),
        )
    return identity, body


def _yesterday(constants: Constants) -> datetime:
    return datetime.now(UTC) - timedelta(hours=constants[R.ENERGY_METER_PERIOD])
