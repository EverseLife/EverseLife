"""Телеметрия и проверки инвариантов (60-meta/04, 30-economy/05).

Проверяется то, ради чего телеметрия вообще заводится **до** плейтеста:

* деньги целы: сумма всех проводок — ноль, а вся масса выпущена генезисом.
  Это не метрика, а закон, и он проверяется автоматически;
* материя считается вся, где бы ни лежала: карман, узел, терминал;
* суточный срез идемпотентен — повтор тика не плодит вторую строку;
* там, где вольт не задал коридора, вердикта нет: зелёная галочка на
  непроверенном хуже отсутствия проверки.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.engine import ledger, world
from src.models.ledger import AccountKind, PostingReason
from src.models.metrics import DailyMetric
from src.telemetry import metrics
from src.units import money


async def _мир(session: AsyncSession, *, денег: float = 100, руды: float = 30):
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.m.{метка}", "Узел", area_m2=100)
    identity = await world.create_identity(session, f"Житель-{метка}")
    body = await world.print_body(session, identity, node)
    карман = await world.body_container(session, body)
    if руды:
        await world.grant_item(
            session, карман, "Железная руда", amount=руды, quality=60, origin="тест"
        )
    if денег:
        счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session, PostingReason.GENESIS, debit=genesis.id, credit=счёт.id,
            amount=money(денег), memo={},
        )
    return node, identity, body


# --- деньги -----------------------------------------------------------------


async def test_двойная_запись_цела(
    session: AsyncSession, constants: Constants
) -> None:
    """Сумма всех проводок — ноль. Иначе деньги появились или исчезли."""
    await _мир(session, денег=250)
    проверки = {п["code"]: п for п in await metrics.invariants(session, constants)}

    запись = проверки["деньги.двойная-запись"]
    assert запись["ok"] is True, запись
    assert запись["value"] == 0

    эмиссия = проверки["деньги.эмиссия"]
    assert эмиссия["ok"] is True, "вся масса выпущена генезисом и только им"


async def test_масса_денег_и_распределение(
    session: AsyncSession, constants: Constants
) -> None:
    """Медиана и Джини считаются по счетам личностей."""
    await _мир(session, денег=100, руды=0)
    await _мир(session, денег=300, руды=0)

    срез = await metrics.collect(session, constants)
    assert срез["money.total"] >= 400
    assert срез["money.median"] > 0
    assert 0 <= срез["money.gini"] <= 1


def test_джини_у_равных_ноль_а_у_одного_владельца_близок_к_единице() -> None:
    """Мера неравенства, а не решение о том, каким ему быть."""
    assert metrics.gini([100, 100, 100]) == pytest.approx(0, abs=0.01)
    assert metrics.gini([0, 0, 300]) > 0.6
    assert metrics.gini([]) == 0


# --- материя ----------------------------------------------------------------


async def test_запас_считается_везде_где_лежит(
    session: AsyncSession, constants: Constants
) -> None:
    """Материя есть материя: карман, узел и терминал считаются вместе."""
    node, _, body = await _мир(session, руды=10)
    двор = await world.node_container(session, node)
    await world.grant_item(session, двор, "Железная руда", amount=5, quality=50, origin="тест")

    остатки = await metrics.stock(session)
    assert остатки["Железная руда"] >= 15


# --- суточный срез ----------------------------------------------------------


async def test_срез_идемпотентен_за_сутки(
    session: AsyncSession, constants: Constants
) -> None:
    """Повтор суточного тика после сбоя не плодит вторую строку."""
    await _мир(session, денег=50)
    момент = datetime.now(UTC)

    сколько = await metrics.store(session, constants, now=момент)
    assert сколько > 0
    строк = await session.scalar(
        select(func.count()).select_from(DailyMetric).where(
            DailyMetric.day == момент.date()
        )
    )

    await metrics.store(session, constants, now=момент)
    снова = await session.scalar(
        select(func.count()).select_from(DailyMetric).where(
            DailyMetric.day == момент.date()
        )
    )
    assert снова == строк, "второй строки за те же сутки не появилось"


async def test_история_помнит_вчера(
    session: AsyncSession, constants: Constants
) -> None:
    """Проверка «растёт две недели подряд» требует памяти, а не выборки."""
    await _мир(session, руды=10)
    сегодня = datetime.now(UTC)
    вчера = сегодня - timedelta(days=1)

    await metrics.store(session, constants, now=вчера)
    await world.grant_item(
        session,
        await world.node_container(
            session, (await _мир(session, денег=0, руды=0))[0]
        ),
        "Железная руда", amount=90, quality=50, origin="тест",
    )
    await metrics.store(session, constants, now=сегодня)

    ряд = await metrics.history(session, "stock.Железная руда", days=5)
    assert len(ряд) == 2
    assert ряд[1][1] > ряд[0][1], "рост запаса виден в истории"


# --- честность проверок -----------------------------------------------------


async def test_у_непроверяемого_нет_вердикта(
    session: AsyncSession, constants: Constants
) -> None:
    """Инварианты без своих систем названы поимённо и без зелёной галочки."""
    проверки = {п["code"]: п for п in await metrics.invariants(session, constants)}

    for код in ("И2", "И4", "И5", "И9", "И12"):
        assert код in проверки, f"{код} обязан быть виден как непроверяемый"
        assert проверки[код]["ok"] is None
        assert "ждёт" in проверки[код]["corridor"]


async def test_и1_показывает_рост_без_выдуманного_порога(
    session: AsyncSession, constants: Constants
) -> None:
    """Порога роста вольт не задал — движок его не выдумывает (D-065)."""
    node, _, _ = await _мир(session, руды=10)
    сегодня = datetime.now(UTC)
    await metrics.store(session, constants, now=сегодня - timedelta(days=1))
    двор = await world.node_container(session, node)
    await world.grant_item(session, двор, "Железная руда", amount=100, quality=50, origin="тест")
    await metrics.store(session, constants, now=сегодня)

    проверки = {п["code"]: п for п in await metrics.invariants(session, constants)}
    запас = проверки.get("И1.запас.Железная руда")
    assert запас is not None
    assert запас["ok"] is None, "вердикта нет: порог не задан вольтом"
    assert запас["value"] > 0, "рост измерен и показан"
