"""The household meter: who pays and what happens if they do not (D-135, D-149).

Checked is exactly what the meter exists for:

* the **holder** pays, and for civic -- the treasury, and not in money but in
  energy it could have sold;
* an unowned node produces no bill at all: nobody to pay, and money has
  nowhere to vanish (I2);
* did not pay -- the node is disconnected, and its machines do not work until payment;
* outside a city there is no meter: there is no grid.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import craft, energy, ledger, utility, world
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer
from src.units import money


async def _city(session: AsyncSession, catalog: Catalog):
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session, f"terra.city.{stamp}", "Столица", area_m2=1,
        layer=Layer.PLANET, parent=planet,
    )
    home = await world.create_node(
        session, f"terra.city.{stamp}.home", "Дом", area_m2=100, parent=delegate
    )
    city = await town.found(session, catalog, delegate, "Столица")
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
            session, PostingReason.GENESIS,
            debit=genesis.id, credit=account.id, amount=money(funds),
        )
    return identity, body


def _yesterday(constants: Constants) -> datetime:
    return datetime.now(UTC) - timedelta(hours=constants[R.ENERGY_METER_PERIOD])


async def test_ownerless_node_has_no_meter(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Nobody to bill -- so there is no meter either."""
    _, _, home = await _city(session, catalog)
    home.owner_city_id = None
    await session.flush()
    assert await utility.meter_of(session, home) is None


async def test_no_meter_outside_city(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """There is no grid there: one works from a battery, and there are no utility relations."""
    identity = await world.create_identity(session, f"Ферма-{uuid.uuid4().hex[:6]}")
    floodplain = await world.create_node(
        session, f"terra.wild.{uuid.uuid4().hex[:8]}", "Пойма", area_m2=400,
        layer=Layer.PLANET,
    )
    floodplain.owner_identity_id = identity.id
    await session.flush()
    assert await utility.meter_of(session, floodplain) is None


async def test_holder_pays_for_household(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The bill is computed from area and written off at the city tariff (D-135)."""
    city, delegate, home = await _city(session, catalog)
    owner, _ = await _resident(session, home, "Хозяин", funds=100)
    home.owner_identity_id = owner.id
    await session.flush()

    pool = await _pool(session, constants, home, 100_000)
    meter = await utility.meter_of(session, home)
    meter.counted_at = _yesterday(constants)
    await session.flush()

    accrued = await utility.bill(session, constants, home)
    assert accrued > 0

    account = await ledger.account_for(session, AccountKind.IDENTITY, owner.id)
    assert await ledger.balance(session, account.id) == money(100) - accrued
    #: Money went to the city treasury, energy from the pool: the meter does
    #: not invent the spend, it writes it off.
    assert await town.treasury_balance(session, city) == accrued
    assert float(pool.stored) < 100_000
    assert not meter.cut_off


async def test_treasury_pays_for_civic_with_energy(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The city does not pay itself in money, but pays in energy (D-149)."""
    city, delegate, home = await _city(session, catalog)
    pool = await _pool(session, constants, home, 100_000)
    before = float(pool.stored)

    meter = await utility.meter_of(session, home)
    meter.counted_at = _yesterday(constants)
    await session.flush()

    assert await utility.bill(session, constants, home) == 0, "казна не платит себе"
    assert float(pool.stored) < before, "энергия всё равно ушла"
    assert await town.treasury_balance(session, city) == 0


async def test_node_disconnected_when_unable_to_pay(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The debt stays on the node, the node is disconnected. The engine may not take it."""
    city, delegate, home = await _city(session, catalog)
    owner, body = await _resident(session, home, "Бедняк")
    home.owner_identity_id = owner.id
    await session.flush()

    await _pool(session, constants, home, 100_000)
    meter = await utility.meter_of(session, home)
    meter.counted_at = _yesterday(constants)
    await session.flush()

    accrued = await utility.bill(session, constants, home)
    assert accrued > 0
    assert meter.cut_off and meter.debt == accrued
    assert await utility.cut_off(session, home)

    #: A disconnected node does not run machines: the meter is as much a
    #: condition of work as the machine itself (D-149).
    yard = await world.node_container(session, home)
    await world.grant_item(session, yard, "Верстак", quality=60, origin="сценарий теста")
    await world.learn(session, owner, "Брус")
    with pytest.raises(craft.CutOff):
        await craft.plan(session, constants, catalog, body, "Брус", 1)


async def test_payment_reconnects_node(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, delegate, home = await _city(session, catalog)
    owner, _ = await _resident(session, home, "Должник")
    home.owner_identity_id = owner.id
    await session.flush()

    await _pool(session, constants, home, 100_000)
    meter = await utility.meter_of(session, home)
    meter.counted_at = _yesterday(constants)
    await session.flush()
    debt = await utility.bill(session, constants, home)
    assert meter.cut_off

    #: Still no money -- nothing to pay with, and that is a refusal, not silence.
    with pytest.raises(utility.NotEnoughMoney):
        await utility.pay(session, constants, owner, home)

    account = await ledger.account_for(session, AccountKind.IDENTITY, owner.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.GENESIS,
        debit=genesis.id, credit=account.id, amount=debt,
    )
    assert await utility.pay(session, constants, owner, home) == debt
    assert not meter.cut_off and meter.debt == 0
    assert await town.treasury_balance(session, city) == debt


async def test_cannot_pay_foreign_bill(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Other people's bills are paid by contract, not by the engine."""
    _, _, home = await _city(session, catalog)
    owner, _ = await _resident(session, home, "Хозяин")
    foreign, _ = await _resident(session, home, "Чужой", funds=100)
    home.owner_identity_id = owner.id
    await session.flush()
    await _pool(session, constants, home, 100_000)
    meter = await utility.meter_of(session, home)
    meter.counted_at = _yesterday(constants)
    await session.flush()
    await utility.bill(session, constants, home)

    with pytest.raises(utility.UtilityError):
        await utility.pay(session, constants, foreign, home)


async def test_meter_opens_itself_on_occupied_nodes(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Otherwise the first bill has nowhere to come from: the meter would wait for itself."""
    _, _, home = await _city(session, catalog)
    assert await utility.meter_of(session, home, create=False) is None
    listed = await utility.run_meters(session, constants)
    assert listed >= 1
    assert await utility.meter_of(session, home, create=False) is not None


async def test_holdings_show_own_nodes(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """An empty list is not a broken panel but "no holdings"."""
    _, _, home = await _city(session, catalog)
    owner, _ = await _resident(session, home, "Хозяин")
    assert await utility.holdings(session, constants, owner.id) == []

    home.owner_identity_id = owner.id
    await session.flush()
    own_items = await utility.holdings(session, constants, owner.id)
    assert len(own_items) == 1
    assert own_items[0]["node"] == home.key
    assert own_items[0]["grid"] is True
    assert own_items[0]["cost_per_period"] > 0
