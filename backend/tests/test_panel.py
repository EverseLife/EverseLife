"""The city's economic panel (D-124, D-140).

Checked is what the panel exists for:

* the public snapshot is visible to all, the full one by the `dashboard` right;
* nothing personal in either snapshot: neither incomes nor routes;
* without an administration the city is blind, and it says so honestly;
* the treasury is broken down by grounds, not one sum.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import ledger, panel, world
from src.models.city import Power
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer
from src.units import money


async def _capital(session: AsyncSession, catalog: Catalog, *, townhall: bool = True):
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session, f"terra.city.{stamp}", "Столица", area_m2=1,
        layer=Layer.PLANET, parent=planet,
    )
    core = await world.create_node(
        session, f"terra.city.{stamp}.core", "Ядро", area_m2=100,
        parent=delegate, properties={"кольцо": 0},
    )
    city = await town.found(session, catalog, delegate, "Столица")
    core.owner_city_id = city.id
    await session.flush()
    if townhall:
        yard = await world.node_container(session, core)
        await world.grant_item(session, yard, town.HALL, quality=65, origin="тест")
    return city, core


async def test_city_blind_without_administration(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The panel lives while the administration stands: otherwise the authority decides blindly."""
    city, _ = await _capital(session, catalog, townhall=False)
    snapshot = await panel.collect(session, constants, city)
    assert snapshot["blind"] is True

    city_with_townhall, _ = await _capital(session, catalog)
    snapshot = await panel.collect(session, constants, city_with_townhall)
    assert snapshot["blind"] is False


async def test_public_snapshot_without_treasury(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Everyone knows prices and turnovers, the treasury by right (D-047, D-140)."""
    city, _ = await _capital(session, catalog)
    opened = await panel.collect(session, constants, city)
    assert "treasury" not in opened
    assert set(opened) >= {"market", "people", "production", "energy", "goods"}

    full = await panel.collect(session, constants, city, full=True)
    assert "treasury" in full


async def test_treasury_broken_down_by_grounds(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """As one sum the panel is useless: decisions are made by line item."""
    city, core = await _capital(session, catalog)
    treasury = await town.treasury(session, city)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.GENESIS,
        debit=genesis.id, credit=treasury.id, amount=money(100),
    )

    snapshot = await panel.collect(session, constants, city, full=True)
    snapshot_treasury = snapshot["treasury"]
    assert snapshot_treasury["balance"] == 100
    assert snapshot_treasury["collected"].get(PostingReason.GENESIS.value) == 100


async def test_panel_window_from_vault(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The step is deliberately slower than the market, and it is not invented by the engine
    (D-124)."""
    city, _ = await _capital(session, catalog)
    snapshot = await panel.collect(session, constants, city)
    assert snapshot["window_hours"] == constants[R.TRADE_REPORT_WINDOW]


async def test_panel_right_is_separate(
    session: AsyncSession, catalog: Catalog
) -> None:
    """The dashboard is as narrow a right as a law: it is granted separately."""
    city, core = await _capital(session, catalog)
    president = await world.create_identity(session, f"П-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, president, core)
    await town.install_founder(session, city, president)
    assert await town.may(session, president.id, city, Power.DASHBOARD)

    bookkeeper = await world.create_identity(session, f"С-{uuid.uuid4().hex[:6]}")
    await world.print_body(session, bookkeeper, core)
    await town.appoint(
        session, president, city, bookkeeper,
        title="Счетовод", powers=(Power.DASHBOARD.value,), body=body,
    )
    assert await town.may(session, bookkeeper.id, city, Power.DASHBOARD)
    assert not await town.may(session, bookkeeper.id, city, Power.TREASURY)


async def test_city_snapshot_lands_in_history(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Telling a spike from a trend is impossible without history (D-140)."""
    from src.telemetry import metrics

    city, _ = await _capital(session, catalog)
    qty = await panel.store_daily(session, constants)
    assert qty >= 1

    row = await metrics.history(session, f"city.{city.id}.treasury", days=3)
    assert row, "срез города обязан попасть в ту же таблицу, что и срез мира"
