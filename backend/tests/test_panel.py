"""Экономическая панель города (D-124, D-140).

Проверяется то, ради чего панель заведена:

* публичный срез виден всем, полный — по праву `dashboard`;
* персонального нет ни в одном срезе: ни доходов, ни маршрутов;
* без администрации город слеп, и он об этом честно говорит;
* казна разложена по основаниям, а не одной суммой.
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


async def _столица(session: AsyncSession, catalog: Catalog, *, ратуша: bool = True):
    метка = uuid.uuid4().hex[:8]
    планета = await world.create_node(
        session, f"terra.{метка}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    представитель = await world.create_node(
        session, f"terra.city.{метка}", "Столица", area_m2=1,
        layer=Layer.PLANET, parent=планета,
    )
    ядро = await world.create_node(
        session, f"terra.city.{метка}.core", "Ядро", area_m2=100,
        parent=представитель, properties={"кольцо": 0},
    )
    город = await town.found(session, catalog, представитель, "Столица")
    ядро.owner_city_id = город.id
    await session.flush()
    if ратуша:
        двор = await world.node_container(session, ядро)
        await world.grant_item(session, двор, town.HALL, quality=65, origin="тест")
    return город, ядро


async def test_без_администрации_город_слеп(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Панель живёт, пока стоит администрация: иначе власть решает вслепую."""
    город, _ = await _столица(session, catalog, ратуша=False)
    срез = await panel.collect(session, constants, город)
    assert срез["blind"] is True

    город_с_ратушей, _ = await _столица(session, catalog)
    срез = await panel.collect(session, constants, город_с_ратушей)
    assert срез["blind"] is False


async def test_публичный_срез_без_казны(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Цены и обороты знают все, казна — по праву (D-047, D-140)."""
    город, _ = await _столица(session, catalog)
    открытый = await panel.collect(session, constants, город)
    assert "treasury" not in открытый
    assert set(открытый) >= {"market", "people", "production", "energy", "goods"}

    полный = await panel.collect(session, constants, город, full=True)
    assert "treasury" in полный


async def test_казна_разложена_по_основаниям(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Одной суммой панель бесполезна: решать нужно по статьям."""
    город, ядро = await _столица(session, catalog)
    казна = await town.treasury(session, город)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.GENESIS,
        debit=genesis.id, credit=казна.id, amount=money(100),
    )

    срез = await panel.collect(session, constants, город, full=True)
    казна_среза = срез["treasury"]
    assert казна_среза["balance"] == 100
    assert казна_среза["collected"].get(PostingReason.GENESIS.value) == 100


async def test_окно_панели_из_вольта(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Шаг медленнее рынка нарочно, и он не выдуман движком (D-124)."""
    город, _ = await _столица(session, catalog)
    срез = await panel.collect(session, constants, город)
    assert срез["window_hours"] == constants[R.TRADE_REPORT_WINDOW]


async def test_право_на_панель_отдельное(
    session: AsyncSession, catalog: Catalog
) -> None:
    """Дашборд — такое же точечное право, как и закон: его выдают отдельно."""
    город, ядро = await _столица(session, catalog)
    президент = await world.create_identity(session, f"П-{uuid.uuid4().hex[:6]}")
    тело = await world.print_body(session, президент, ядро)
    await town.install_founder(session, город, президент)
    assert await town.may(session, президент.id, город, Power.DASHBOARD)

    счетовод = await world.create_identity(session, f"С-{uuid.uuid4().hex[:6]}")
    await world.print_body(session, счетовод, ядро)
    await town.appoint(
        session, президент, город, счетовод,
        title="Счетовод", powers=(Power.DASHBOARD.value,), body=тело,
    )
    assert await town.may(session, счетовод.id, город, Power.DASHBOARD)
    assert not await town.may(session, счетовод.id, город, Power.TREASURY)


async def test_срез_города_ложится_в_историю(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Отличать всплеск от тенденции без истории невозможно (D-140)."""
    from src.telemetry import metrics

    город, _ = await _столица(session, catalog)
    сколько = await panel.store_daily(session, constants)
    assert сколько >= 1

    ряд = await metrics.history(session, f"city.{город.id}.treasury", days=3)
    assert ряд, "срез города обязан попасть в ту же таблицу, что и срез мира"
