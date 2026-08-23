# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Telemetry and invariant checks (60-meta/04, 30-economy/05).

In an economic game without telemetry you are blind: the economy breaks
quietly and is discovered a month later, when it is too late to fix. Hence
the vault's rule: **every invariant must have an automatic check**, not a
report somebody will look at.

## What is measured here

**Matter** -- the stock of each resource in the world, whole: pockets, nodes,
terminals, hoppers. By it invariant I1 is seen: inflow ~ outflow on a
one-week horizon. A stock growing for weeks is an early sign of the economy's death.

**Money** -- supply, distribution (median, top-percent share, Gini) and
double-entry integrity. The last is not a metric but a check: the sum of all
postings must be zero, and the whole supply must be issued by genesis. No
process but it creates TC (01-currency).

**Prices** -- by concluded deals: median and volume. Prices are public (D-047),
there is no reason to hide them; the only internal thing here is that we
store them by day.

## What is deliberately not here

**Verdicts where the vault set no corridor.** I1 demands the signal "stock
grows more than N% a week", but N itself is not in the vault -- so the engine
shows the measured growth and honestly says no threshold is set. Inventing one
here would introduce a balance number past the vault (D-065).

**Per-player metrics by name.** World aggregates go out; the personal is the
subject of a separate privacy decision (open question 60-meta/04).

**Invariants whose systems do not exist yet:** I4 (delivery) waits for
transport, I5 (maintenance) for buildings, I9 and I13 for death and body
printing, I12 for the bank. Their checks appear together with them, not
earlier: checking what does not exist is a green tick fooling itself.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import bank
from src.models.identity import Identity
from src.models.inventory import Item
from src.models.ledger import AccountKind, LedgerAccount, LedgerEntry
from src.models.market import Trade
from src.models.metrics import DailyMetric
from src.models.world import Vein
from src.units import AMOUNT_SCALE, MONEY_SCALE, PERCENT, amount_float

#: The basket of base goods for the price index. Composition from vault data:
#: the raw material the whole ladder stands on (`raw` in recipes.json).
BASKET_LIMIT = 8


async def stock(session: AsyncSession) -> dict[str, float]:
    """The stock of each resource in the world -- all matter, wherever it lies."""
    rows = await session.execute(
        select(Item.type_key, func.sum(Item.amount)).group_by(Item.type_key)
    )
    return {name: amount_float(int(qty)) for name, qty in rows.all()}


async def money_supply(session: AsyncSession) -> int:
    """The TC supply on identity accounts, in minor units."""
    result = await session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(LedgerAccount, LedgerAccount.id == LedgerEntry.account_id)
        .where(LedgerAccount.kind == AccountKind.IDENTITY)
    )
    return int(result or 0)


async def balances(session: AsyncSession) -> list[int]:
    """Each identity's balance. The basis of distribution -- medians and Gini."""
    rows = await session.execute(
        select(LedgerAccount.owner_id, func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(LedgerEntry, LedgerEntry.account_id == LedgerAccount.id, isouter=True)
        .where(LedgerAccount.kind == AccountKind.IDENTITY)
        .group_by(LedgerAccount.owner_id)
    )
    return sorted(int(balance) for _, balance in rows.all())


def gini(values: list[int]) -> float:
    """The Gini coefficient: 0 -- all equal, 1 -- everything with one.

    The formula is standard and is not a balance number: it is a way to
    measure, not a decision about what inequality should be.
    """
    if_empty = 0.0
    row = sorted(v for v in values if v >= 0)
    n = len(row)
    total = sum(row)
    if n == 0 or total == 0:
        return if_empty
    accumulated = sum((i + 1) * v for i, v in enumerate(row))
    return (2 * accumulated) / (n * total) - (n + 1) / n


def median(values: list[int]) -> float:
    row = sorted(values)
    if not row:
        return 0.0
    middle = len(row) // 2
    if len(row) % 2:
        return float(row[middle])
    return (row[middle - 1] + row[middle]) / 2


async def prices(session: AsyncSession, *, since: datetime) -> dict[str, float]:
    """The median price of each goods from concluded deals."""
    rows = (
        await session.execute(select(Trade.type_key, Trade.price).where(Trade.at >= since))
    ).all()
    by_goods: dict[str, list[int]] = {}
    for name, price in rows:
        by_goods.setdefault(name, []).append(int(price))
    return {name: median(price_map) / MONEY_SCALE for name, price_map in by_goods.items()}


async def collect(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> dict[str, float]:
    """Take all measurements of the world as of now."""
    moment = now or datetime.now(UTC)
    day = timedelta(hours=constants[R.TIME_DAY_TERRA])

    #: The price index is a monetary-policy sensor (D-087, D-169). The bank
    #: computes it: one formula for the panel, the dashboard and the rate, no second copy.

    index = await bank.price_index(session, constants, now=moment)

    remainders = await stock(session)
    accounts = await balances(session)
    price_map = await prices(session, since=moment - day)

    deal_count = await session.scalar(
        select(func.count()).select_from(Trade).where(Trade.at >= moment - day)
    )
    turnover = await session.scalar(
        select(func.coalesce(func.sum(Trade.price * Trade.amount), 0)).where(
            Trade.at >= moment - day
        )
    )
    veins = await session.scalar(select(func.coalesce(func.sum(Vein.remaining), 0)))
    people_ = await session.scalar(select(func.count()).select_from(Identity))

    result: dict[str, float] = {
        "money.total": await money_supply(session) / MONEY_SCALE,
        "money.median": median(accounts) / MONEY_SCALE,
        "money.gini": gini(accounts),
        "people": float(people_ or 0),
        "trades.count": float(deal_count or 0),
        #: Turnover is counted in minor units per internal units of goods.
        "trades.volume": float(turnover or 0) / MONEY_SCALE / AMOUNT_SCALE,
        "veins.remaining": amount_float(int(veins or 0)),
    }
    for name, qty in remainders.items():
        result[f"stock.{name}"] = qty
    for name, price in price_map.items():
        result[f"price.{name}"] = price
    #: The index is the only measurement read not by a human but by monetary
    #: policy: inflation is computed from it (D-169).
    if index is not None:
        result[bank.PRICE_INDEX] = index / MONEY_SCALE
    return result


async def store(session: AsyncSession, constants: Constants, *, now: datetime | None = None) -> int:
    """Write the daily snapshot. A repeat for the same day overwrites the value.

    Idempotent on purpose: the daily tick may repeat after a failure, and no
    second row for the same day must come of it.
    """
    moment = now or datetime.now(UTC)
    values = await collect(session, constants, now=moment)
    return await remember(session, values, now=moment)


async def remember(
    session: AsyncSession,
    values: dict[str, float],
    *,
    now: datetime | None = None,
) -> int:
    """Store measurements into the daily snapshot. Returns the number written.

    Separate from `collect` because there is more than one writer: the world
    snapshot and each city's snapshot (D-140) go into one table -- otherwise
    city history would need a second one, diverging from the first.
    """
    moment = now or datetime.now(UTC)
    day_ = moment.date()
    previous_ones = {
        row.key: row
        for row in (await session.execute(select(DailyMetric).where(DailyMetric.day == day_)))
        .scalars()
        .all()
    }
    for key, value in values.items():
        line = previous_ones.get(key)
        if line is None:
            session.add(DailyMetric(day=day_, key=key, value=value))
        else:
            line.value = value
    await session.flush()
    return len(values)


async def history(session: AsyncSession, key: str, *, days: int = 14) -> list[tuple[date, float]]:
    """What this measurement was over the last day -- for a trend."""
    rows = (
        await session.execute(
            select(DailyMetric.day, DailyMetric.value)
            .where(DailyMetric.key == key)
            .order_by(DailyMetric.day.desc())
            .limit(days)
        )
    ).all()
    return [(day_, float(value)) for day_, value in reversed(rows)]


async def invariants(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> list[dict]:
    """Invariant checks. A verdict only where the vault set a corridor.

    Returns a list: code, what was measured, verdict and why it matters. Where
    the system does not exist yet or no corridor is set, the verdict is
    "observation": a green tick on the unchecked is worse than no check.
    """
    result: list[dict] = []

    #: Double-entry integrity. Not a metric but a law: the sum of all postings
    #: is zero, otherwise money appeared or vanished somewhere (01-tech-notes, pattern 2).
    postings_total = int(
        await session.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0))) or 0
    )
    result.append(
        {
            "code": "деньги.двойная-запись",
            "value": postings_total / MONEY_SCALE,
            "ok": postings_total == 0,
            "corridor": "ровно 0",
            "why": "сумма всех проводок обязана быть нулём: иначе деньги появились или исчезли",
        }
    )

    #: The whole TC supply must be issued by genesis and only by it.
    emitted = int(
        await session.scalar(
            select(func.coalesce(func.sum(-LedgerEntry.amount), 0))
            .join(LedgerAccount, LedgerAccount.id == LedgerEntry.account_id)
            .where(LedgerAccount.kind == AccountKind.GENESIS)
        )
        or 0
    )
    in_circulation = int(
        await session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount), 0))
            .join(LedgerAccount, LedgerAccount.id == LedgerEntry.account_id)
            .where(LedgerAccount.kind != AccountKind.GENESIS)
        )
        or 0
    )
    result.append(
        {
            "code": "деньги.эмиссия",
            "value": (in_circulation - emitted) / MONEY_SCALE,
            "ok": in_circulation == emitted,
            "corridor": "разница 0",
            "why": "ТК появляется только через генезис (позже — через кредит): "
            "всё остальное было бы печатным станком",
        }
    )

    #: I1: a resource's stock must not grow for weeks. The vault set no growth
    #: threshold -- we show the measured and say so directly.
    for key, resource_ in sorted((f"stock.{name}", name) for name in (await stock(session)))[
        :BASKET_LIMIT
    ]:
        row = await history(session, key, days=8)
        if len(row) < 2:
            continue
        before, after = row[0][1], row[-1][1]
        growth = (after - before) / before * PERCENT if before else 0.0
        result.append(
            {
                "code": f"И1.запас.{resource_}",
                "value": round(growth, 1),
                "ok": None,
                "corridor": "порог роста в вольте не задан",
                "why": "приток ≈ отток на горизонте недели: запас, растущий "
                "неделями, — ранний признак смерти экономики",
            }
        )

    #: Invariants whose systems do not exist yet. Named by name so that the
    #: absence of a check is visible rather than implied.

    for code, waiting_for in (
        ("И2", "дохода по профессиям: учёта труда ещё нет"),
        ("И3", "капитала: аренда и доли приедут с договорами"),
        ("И4", "транспорта: стоимости доставки пока не существует"),
        ("И5", "содержания: построек и их упкипа ещё нет"),
        ("И9", "печати тела: смерть приезжает с Э3"),
        ("И12", "банка и ключевой ставки (Э4)"),
    ):
        result.append(
            {
                "code": code,
                "value": None,
                "ok": None,
                "corridor": f"ждёт {waiting_for}",
                "why": "проверка того, чего нет, — зелёная галочка, обманывающая себя",
            }
        )
    return result
