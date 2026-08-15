"""Telemetry and invariant checks (60-meta/04, 30-economy/05).

Checked is what telemetry is created for at all **before** the playtest:

* money is intact: the sum of all postings is zero, and the whole supply is
  issued by genesis. That is not a metric but a law, and it is checked automatically;
* all matter is counted, wherever it lies: pocket, node, terminal;
* the daily snapshot is idempotent -- a tick repeat does not spawn a second row;
* where the vault set no corridor, there is no verdict: a green tick on the
  unchecked is worse than no check.
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


async def _world(session: AsyncSession, *, funds: float = 100, ore: float = 30):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.m.{stamp}", "Узел", area_m2=100)
    identity = await world.create_identity(session, f"Житель-{stamp}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    if ore:
        await world.grant_item(
            session, pocket, "Железная руда", amount=ore, quality=60, origin="тест"
        )
    if funds:
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session, PostingReason.GENESIS, debit=genesis.id, credit=account.id,
            amount=money(funds), memo={},
        )
    return node, identity, body


# --- money -------------------------------------------------------------------


async def test_double_entry_intact(
    session: AsyncSession, constants: Constants
) -> None:
    """The sum of all postings is zero. Otherwise money appeared or vanished."""
    await _world(session, funds=250)
    checks = {p["code"]: p for p in await metrics.invariants(session, constants)}

    entry = checks["деньги.двойная-запись"]
    assert entry["ok"] is True, entry
    assert entry["value"] == 0

    emission = checks["деньги.эмиссия"]
    assert emission["ok"] is True, "вся масса выпущена генезисом и только им"


async def test_money_supply_and_distribution(
    session: AsyncSession, constants: Constants
) -> None:
    """Median and Gini are computed over identity accounts."""
    await _world(session, funds=100, ore=0)
    await _world(session, funds=300, ore=0)

    snapshot = await metrics.collect(session, constants)
    assert snapshot["money.total"] >= 400
    assert snapshot["money.median"] > 0
    assert 0 <= snapshot["money.gini"] <= 1


def test_gini_zero_for_equals_and_near_one_for_single_owner() -> None:
    """A measure of inequality, not a decision about what it should be."""
    assert metrics.gini([100, 100, 100]) == pytest.approx(0, abs=0.01)
    assert metrics.gini([0, 0, 300]) > 0.6
    assert metrics.gini([]) == 0


# --- matter ------------------------------------------------------------------


async def test_stock_counted_wherever_it_lies(
    session: AsyncSession, constants: Constants
) -> None:
    """Matter is matter: pocket, node and terminal are counted together."""
    node, _, body = await _world(session, ore=10)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "Железная руда", amount=5, quality=50, origin="тест")

    remainders = await metrics.stock(session)
    assert remainders["Железная руда"] >= 15


# --- daily snapshot ----------------------------------------------------------


async def test_snapshot_idempotent_per_day(
    session: AsyncSession, constants: Constants
) -> None:
    """A daily tick repeat after a failure does not spawn a second row."""
    await _world(session, funds=50)
    moment = datetime.now(UTC)

    qty = await metrics.store(session, constants, now=moment)
    assert qty > 0
    line_count = await session.scalar(
        select(func.count()).select_from(DailyMetric).where(
            DailyMetric.day == moment.date()
        )
    )

    await metrics.store(session, constants, now=moment)
    again = await session.scalar(
        select(func.count()).select_from(DailyMetric).where(
            DailyMetric.day == moment.date()
        )
    )
    assert again == line_count, "второй строки за те же сутки не появилось"


async def test_history_remembers_yesterday(
    session: AsyncSession, constants: Constants
) -> None:
    """The check "grows two weeks in a row" needs memory, not a query."""
    await _world(session, ore=10)
    today = datetime.now(UTC)
    yesterday = today - timedelta(days=1)

    await metrics.store(session, constants, now=yesterday)
    await world.grant_item(
        session,
        await world.node_container(
            session, (await _world(session, funds=0, ore=0))[0]
        ),
        "Железная руда", amount=90, quality=50, origin="тест",
    )
    await metrics.store(session, constants, now=today)

    row = await metrics.history(session, "stock.Железная руда", days=5)
    assert len(row) == 2
    assert row[1][1] > row[0][1], "рост запаса виден в истории"


# --- honesty of checks -------------------------------------------------------


async def test_unverifiable_has_no_verdict(
    session: AsyncSession, constants: Constants
) -> None:
    """Invariants without their systems are named by name and without a green tick."""
    checks = {p["code"]: p for p in await metrics.invariants(session, constants)}

    for code in ("И2", "И4", "И5", "И9", "И12"):
        assert code in checks, f"{code} обязан быть виден как непроверяемый"
        assert checks[code]["ok"] is None
        assert "ждёт" in checks[code]["corridor"]


async def test_i1_shows_growth_without_invented_threshold(
    session: AsyncSession, constants: Constants
) -> None:
    """The vault set no growth threshold -- the engine does not invent one (D-065)."""
    node, _, _ = await _world(session, ore=10)
    today = datetime.now(UTC)
    await metrics.store(session, constants, now=today - timedelta(days=1))
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "Железная руда", amount=100, quality=50, origin="тест")
    await metrics.store(session, constants, now=today)

    checks = {p["code"]: p for p in await metrics.invariants(session, constants)}
    stock = checks.get("И1.запас.Железная руда")
    assert stock is not None
    assert stock["ok"] is None, "вердикта нет: порог не задан вольтом"
    assert stock["value"] > 0, "рост измерен и показан"
