# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The meter tests' shared ground: a city with a grid, a plot in it, a charged
pool and a resident with money (D-135, D-149).

Used by `test_utility.py` and the meter's race files, `test_races_meter.py`
and `test_races_meter_land.py`; not collected by pytest.
No real fixture lives here on purpose -- a `@pytest.fixture` in a kit is
imported for its name alone, ruff removes the import as unused, and pytest
then cannot find it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import energy, ledger, utility, world
from src.models.city import UtilityMeter
from src.models.ledger import AccountKind, PostingReason
from src.models.world import PLOT, Layer, Node
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


async def _grids(session: AsyncSession, constants: Constants, catalog: Catalog, count: int):
    """Cities with a charged pool each and a plot in each, in their grid node's order.

    Each treasury's account is opened here: opened by the first bill instead,
    two transactions would meet on its insert, and the race would be about
    that row rather than the order under test.
    """
    made = []
    for number in range(count):
        _, delegate, home = await _city(session, catalog, name=f"Столица {number}")
        await _pool(session, constants, home, 100_000)
        await ledger.account_for(session, AccountKind.CITY_TREASURY, delegate.id)
        made.append((delegate, home))
    return sorted(made, key=lambda one: one[0].id)


async def _open(
    session: AsyncSession, constants: Constants, homes: list[Node], since: datetime
) -> list[UtilityMeter]:
    """Open the meters one by one in this order, each counted from `since`.

    Written in this order and with ids sorting in it too: a table read with no
    `order_by` comes back in the order its rows were written, and a run that
    walks its meters by id alone goes the same way -- so either is the order
    a race sets against the pools' or the purses'.
    """
    meters = []
    for meter_id, home in zip(sorted(uuid.uuid4() for _ in homes), homes, strict=True):
        meter = UtilityMeter(id=meter_id, node_id=home.id, counted_at=since)
        session.add(meter)
        await session.flush()
        meters.append(meter)
    #: Nothing else in these worlds carries a meter, so no other meter slips
    #: into the run's order.
    assert await utility.ensure_meters(session, constants) == 0
    return meters


async def _purse(factory: async_sessionmaker[AsyncSession], identity_id) -> int:
    """The identity's purse as committed."""
    async with factory() as db:
        account = await ledger.find_account(db, AccountKind.IDENTITY, identity_id)
        assert account is not None
        return await ledger.balance(db, account.id)


async def _meter(factory: async_sessionmaker[AsyncSession], meter_id) -> UtilityMeter:
    """The meter as committed."""
    async with factory() as db:
        meter = await db.get(UtilityMeter, meter_id)
        assert meter is not None
        return meter


async def _held_by(factory: async_sessionmaker[AsyncSession], node_id, meter_id):
    """Who holds the node, and what its meter owes: `(holder, debt, cut_off)`."""
    async with factory() as db:
        node = await db.get(Node, node_id)
        meter = await db.get(UtilityMeter, meter_id)
        assert node is not None and meter is not None
        return node.owner_identity_id, meter.debt, meter.cut_off
