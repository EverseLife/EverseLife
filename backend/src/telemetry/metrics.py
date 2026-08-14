"""Телеметрия и проверки инвариантов (60-meta/04, 30-economy/05).

В экономической игре без телеметрии ты слепой: экономика ломается тихо и
обнаруживается через месяц, когда исправлять уже поздно. Отсюда правило вольта:
**каждый инвариант обязан иметь автоматическую проверку**, а не отчёт, который
кто-то посмотрит.

## Что здесь считается

**Материя** — запас каждого ресурса в мире, целиком: карманы, узлы, терминалы,
бункеры. По нему и виден инвариант И1: приток ≈ отток на горизонте недели.
Запас, растущий неделями, — ранний признак смерти экономики.

**Деньги** — масса, распределение (медиана, доля верхнего процента, Джини) и
целость двойной записи. Последнее не метрика, а проверка: сумма всех проводок
обязана быть нулём, а вся масса — выпущенной генезисом. Ни один процесс, кроме
него, ТК не создаёт (01-currency).

**Цены** — по состоявшимся сделкам: медиана и объём. Цены публичны (D-047), и
прятать их незачем; служебное здесь только то, что мы их складываем по суткам.

## Чего здесь намеренно нет

**Вердиктов там, где вольт не задал коридора.** И1 требует сигнала «запас растёт
больше N% в неделю», но самого N в вольте нет — значит движок показывает
измеренный рост и честно говорит, что порога не задано. Придумать его здесь
значило бы завести балансное число мимо вольта (D-065).

**Метрик по игрокам поимённо.** Наружу уходят агрегаты мира; персональное —
предмет отдельного решения о приватности (открытый вопрос 60-meta/04).

**Инвариантов, чьих систем ещё нет:** И4 (доставка) ждёт транспорта, И5
(содержание) — построек, И9 и И13 — смерти и печати тел, И12 — банка. Их
проверки появятся вместе с ними, а не раньше: проверка того, чего нет, — это
зелёная галочка, обманывающая себя.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.models.identity import Identity
from src.models.inventory import Item
from src.models.ledger import AccountKind, LedgerAccount, LedgerEntry
from src.models.market import Trade
from src.models.metrics import DailyMetric
from src.models.world import Vein
from src.units import AMOUNT_SCALE, MONEY_SCALE, PERCENT, amount_float

#: Корзина базовых товаров для индекса цен. Состав — из данных вольта:
#: сырьё, на котором стоит вся лестница (`raw` в recipes.json).
BASKET_LIMIT = 8


async def stock(session: AsyncSession) -> dict[str, float]:
    """Запас каждого ресурса в мире — вся материя, где бы она ни лежала."""
    rows = await session.execute(
        select(Item.type_key, func.sum(Item.amount)).group_by(Item.type_key)
    )
    return {имя: amount_float(int(сколько)) for имя, сколько in rows.all()}


async def money_supply(session: AsyncSession) -> int:
    """Масса ТК на счетах личностей, в минорных единицах."""
    итог = await session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(LedgerAccount, LedgerAccount.id == LedgerEntry.account_id)
        .where(LedgerAccount.kind == AccountKind.IDENTITY)
    )
    return int(итог or 0)


async def balances(session: AsyncSession) -> list[int]:
    """Баланс каждой личности. Основа распределения — медианы и Джини."""
    rows = await session.execute(
        select(LedgerAccount.owner_id, func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(LedgerEntry, LedgerEntry.account_id == LedgerAccount.id, isouter=True)
        .where(LedgerAccount.kind == AccountKind.IDENTITY)
        .group_by(LedgerAccount.owner_id)
    )
    return sorted(int(баланс) for _, баланс in rows.all())


def gini(values: list[int]) -> float:
    """Коэффициент Джини: 0 — все равны, 1 — всё у одного.

    Формула стандартная и балансным числом не является: это способ измерить, а
    не решение о том, каким неравенство должно быть.
    """
    если_пусто = 0.0
    ряд = sorted(v for v in values if v >= 0)
    n = len(ряд)
    сумма = sum(ряд)
    if n == 0 or сумма == 0:
        return если_пусто
    накопленное = sum((i + 1) * v for i, v in enumerate(ряд))
    return (2 * накопленное) / (n * сумма) - (n + 1) / n


def median(values: list[int]) -> float:
    ряд = sorted(values)
    if not ряд:
        return 0.0
    середина = len(ряд) // 2
    if len(ряд) % 2:
        return float(ряд[середина])
    return (ряд[середина - 1] + ряд[середина]) / 2


async def prices(session: AsyncSession, *, since: datetime) -> dict[str, float]:
    """Медианная цена по каждому товару из состоявшихся сделок."""
    rows = (
        await session.execute(
            select(Trade.type_key, Trade.price).where(Trade.at >= since)
        )
    ).all()
    по_товарам: dict[str, list[int]] = {}
    for имя, цена in rows:
        по_товарам.setdefault(имя, []).append(int(цена))
    return {имя: median(цены) / MONEY_SCALE for имя, цены in по_товарам.items()}


async def collect(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> dict[str, float]:
    """Снять все измерения мира на сейчас."""
    moment = now or datetime.now(UTC)
    сутки = timedelta(hours=constants[R.TIME_DAY_TERRA])

    #: Индекс цен — датчик денежной политики (D-087, D-169). Считает его банк:
    #: одна формула на панель, дашборд и ставку, второй копии быть не должно.
    from src.engine import bank

    индекс = await bank.price_index(session, constants, now=moment)

    остатки = await stock(session)
    счета = await balances(session)
    цены = await prices(session, since=moment - сутки)

    сделок = await session.scalar(
        select(func.count()).select_from(Trade).where(Trade.at >= moment - сутки)
    )
    оборот = await session.scalar(
        select(func.coalesce(func.sum(Trade.price * Trade.amount), 0)).where(
            Trade.at >= moment - сутки
        )
    )
    жилы = await session.scalar(
        select(func.coalesce(func.sum(Vein.remaining), 0))
    )
    людей = await session.scalar(select(func.count()).select_from(Identity))

    итог: dict[str, float] = {
        "money.total": await money_supply(session) / MONEY_SCALE,
        "money.median": median(счета) / MONEY_SCALE,
        "money.gini": gini(счета),
        "people": float(людей or 0),
        "trades.count": float(сделок or 0),
        #: Оборот считается в минорных единицах на внутренние единицы товара.
        "trades.volume": float(оборот or 0) / MONEY_SCALE / AMOUNT_SCALE,
        "veins.remaining": amount_float(int(жилы or 0)),
    }
    for имя, сколько in остатки.items():
        итог[f"stock.{имя}"] = сколько
    for имя, цена in цены.items():
        итог[f"price.{имя}"] = цена
    #: Индекс — единственное измерение, которое читает не человек, а денежная
    #: политика: по нему считается инфляция (D-169).
    if индекс is not None:
        итог[bank.PRICE_INDEX] = индекс / MONEY_SCALE
    return итог


async def store(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> int:
    """Записать суточный срез. Повтор за те же сутки переписывает значение.

    Идемпотентно нарочно: суточный тик может повториться после сбоя, и второй
    строки за те же сутки из этого выйти не должно.
    """
    moment = now or datetime.now(UTC)
    значения = await collect(session, constants, now=moment)
    return await remember(session, значения, now=moment)


async def remember(
    session: AsyncSession,
    значения: dict[str, float],
    *,
    now: datetime | None = None,
) -> int:
    """Сложить измерения в суточный срез. Возвращает число записанных.

    Отдельно от `collect`, потому что писателей больше одного: срез мира и
    срез каждого города (D-140) кладутся в одну таблицу — иначе история
    городов потребовала бы второй, разошедшейся с первой.
    """
    moment = now or datetime.now(UTC)
    день = moment.date()
    прежние = {
        row.key: row
        for row in (
            await session.execute(select(DailyMetric).where(DailyMetric.day == день))
        ).scalars().all()
    }
    for ключ, значение in значения.items():
        строка = прежние.get(ключ)
        if строка is None:
            session.add(DailyMetric(day=день, key=ключ, value=значение))
        else:
            строка.value = значение
    await session.flush()
    return len(значения)


async def history(
    session: AsyncSession, key: str, *, days: int = 14
) -> list[tuple[date, float]]:
    """Что было по этому измерению последние сутки — для тренда."""
    rows = (
        await session.execute(
            select(DailyMetric.day, DailyMetric.value)
            .where(DailyMetric.key == key)
            .order_by(DailyMetric.day.desc())
            .limit(days)
        )
    ).all()
    return [(день, float(значение)) for день, значение in reversed(rows)]


async def invariants(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> list[dict]:
    """Проверки инвариантов. Вердикт — только там, где вольт задал коридор.

    Возвращает список: код, что измерено, вердикт и почему это важно. Там, где
    системы ещё нет или коридор не задан, вердикт — «наблюдение»: зелёная
    галочка на непроверенном хуже отсутствия проверки.
    """
    moment = now or datetime.now(UTC)
    итог: list[dict] = []

    #: Целость двойной записи. Не метрика, а закон: сумма всех проводок ноль,
    #: иначе деньги где-то появились или исчезли (01-tech-notes, паттерн 2).
    сумма_проводок = int(
        await session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        )
        or 0
    )
    итог.append(
        {
            "code": "деньги.двойная-запись",
            "value": сумма_проводок / MONEY_SCALE,
            "ok": сумма_проводок == 0,
            "corridor": "ровно 0",
            "why": "сумма всех проводок обязана быть нулём: иначе деньги "
            "появились или исчезли",
        }
    )

    #: Вся масса ТК обязана быть выпущена генезисом и только им.
    выпущено = int(
        await session.scalar(
            select(func.coalesce(func.sum(-LedgerEntry.amount), 0))
            .join(LedgerAccount, LedgerAccount.id == LedgerEntry.account_id)
            .where(LedgerAccount.kind == AccountKind.GENESIS)
        )
        or 0
    )
    в_обороте = int(
        await session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount), 0))
            .join(LedgerAccount, LedgerAccount.id == LedgerEntry.account_id)
            .where(LedgerAccount.kind != AccountKind.GENESIS)
        )
        or 0
    )
    итог.append(
        {
            "code": "деньги.эмиссия",
            "value": (в_обороте - выпущено) / MONEY_SCALE,
            "ok": в_обороте == выпущено,
            "corridor": "разница 0",
            "why": "ТК появляется только через генезис (позже — через кредит): "
            "всё остальное было бы печатным станком",
        }
    )

    #: И1: запас ресурса не должен расти неделями. Порога роста вольт не задал —
    #: показываем измеренное и говорим об этом прямо.
    for ключ, ресурс in sorted(
        (f"stock.{имя}", имя) for имя in (await stock(session))
    )[:BASKET_LIMIT]:
        ряд = await history(session, ключ, days=8)
        if len(ряд) < 2:
            continue
        было, стало = ряд[0][1], ряд[-1][1]
        рост = (стало - было) / было * PERCENT if было else 0.0
        итог.append(
            {
                "code": f"И1.запас.{ресурс}",
                "value": round(рост, 1),
                "ok": None,
                "corridor": "порог роста в вольте не задан",
                "why": "приток ≈ отток на горизонте недели: запас, растущий "
                "неделями, — ранний признак смерти экономики",
            }
        )

    #: Инварианты, чьих систем ещё нет. Названы поимённо, чтобы отсутствие
    #: проверки было видно, а не подразумевалось.
    for код, чего_ждёт in (
        ("И2", "дохода по профессиям: учёта труда ещё нет"),
        ("И3", "капитала: аренда и доли приедут с договорами"),
        ("И4", "транспорта: стоимости доставки пока не существует"),
        ("И5", "содержания: построек и их упкипа ещё нет"),
        ("И9", "печати тела: смерть приезжает с Э3"),
        ("И12", "банка и ключевой ставки (Э4)"),
    ):
        итог.append(
            {
                "code": код,
                "value": None,
                "ok": None,
                "corridor": f"ждёт {чего_ждёт}",
                "why": "проверка того, чего нет, — зелёная галочка, "
                "обманывающая себя",
            }
        )
    return итог
