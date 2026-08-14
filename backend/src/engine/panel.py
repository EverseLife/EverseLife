"""Экономическая панель города (D-124, D-140).

Власть получила ставки и запреты, но до сих пор не имела ни одного способа
узнать, что происходит: решение «поднять пошлину» принималось бы по жалобам в
чате, то есть по тому, кто громче кричит. Панель отвечает на вопрос «что
происходит» цифрами, которые движок и так знает.

## Шесть разделов, и все из журнала событий

| Раздел | Откуда берётся |
|---|---|
| **Товары** | баланс: произведено внутри + добыто − потреблено переделом |
| **Рынок** | сделки узлов города: оборот, медианная цена, число сделок |
| **Казна** | проводки счёта города по основаниям: собрано и потрачено |
| **Энергия** | пул, выработка станций, отпуск по счётчику и на работу |
| **Люди** | кто сейчас в городе и кто здесь печатался за период |
| **Производство** | удары в забоях, уборка делянок, готовые партии |

## Три правила, без которых панель вредна

**Шаг медленнее рынка.** Окно — `trade.report_window` часов. Мгновенные данные
превратили бы панель в биржевой терминал и дали бы власти торговое
преимущество перед собственными купцами. Инструмент управления обязан быть
медленнее рынка, которым управляет (D-124).

**Публичный срез виден всем**, включая гостей: балансы, обороты, цены,
население. Это продолжение правила «цены знают все» (D-047). Если цифры видит
только правитель, спорить с ним нечем и выборы превращаются во вкусовщину.
Полный набор — тем, у кого есть право `dashboard`.

**Персонального нет ни у кого.** Ни доходов конкретных игроков, ни маршрутов,
ни связей: иначе городская панель превращается в слежку, а приватность (D-081)
в декорацию.

## Без администрации город слеп

Панель живёт, **пока стоит и содержится администрация** (D-140). Снесли,
отключили за неуплату — данные не обновляются, и власть решает вслепую. Это и
делает постройку осмысленной, и добавляет ступень в порядок распада пустой
казны (D-127).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.models.city import City
from src.models.event import Event, EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.ledger import LedgerEntry, LedgerTransaction
from src.models.market import Trade
from src.models.world import Node
from src.telemetry.metrics import median
from src.units import AMOUNT_SCALE, MONEY_SCALE


async def city_nodes(session: AsyncSession, city: City) -> list[Node]:
    """Территория города: узлы, которыми он владеет."""
    return list(
        (
            await session.execute(select(Node).where(Node.owner_city_id == city.id))
        ).scalars().all()
    )


async def blind(session: AsyncSession, city: City) -> bool:
    """Ослеп ли город: нет администрации либо она отключена (D-140)."""
    from src.engine import city as town
    from src.engine import utility, world

    for узел in await city_nodes(session, city):
        двор = await world.node_container(session, узел)
        стоит = await session.scalar(
            select(Item.id)
            .where(Item.container_id == двор.id, Item.type_key == town.HALL)
            .limit(1)
        )
        if стоит is not None and not await utility.cut_off(session, узел):
            return False
    return True


async def collect(
    session: AsyncSession,
    constants: Constants,
    city: City,
    *,
    full: bool = False,
    now: datetime | None = None,
) -> dict:
    """Снять панель города за окно `trade.report_window`.

    `full` добавляет казну по основаниям — тем, у кого есть право `dashboard`.
    Персонального нет ни в публичном срезе, ни в полном.
    """
    moment = now or datetime.now(UTC)
    окно = timedelta(hours=constants[R.TRADE_REPORT_WINDOW])
    с = moment - окно

    узлы = await city_nodes(session, city)
    ключи = [узел.id for узел in узлы]
    слеп = await blind(session, city)

    сводка: dict = {
        "city": city.name,
        "window_hours": constants[R.TRADE_REPORT_WINDOW],
        "at": moment.isoformat(),
        #: Слепой город отдаёт последнее, что знал, и честно об этом говорит:
        #: молча показывать вчерашние числа как сегодняшние нельзя.
        "blind": слеп,
        "market": await _market(session, ключи, since=с),
        "people": await _people(session, ключи, since=с),
        "production": await _production(session, ключи, since=с),
        "energy": await _energy(session, constants, city, since=с),
        "goods": await _goods(session, ключи, since=с),
        #: Ввоз, вывоз и собранная пошлина — главная строка сводки: по ней
        #: видно, что руда утекает, а не дорожает сама по себе (D-124).
        "trade": await _trade(session, constants, city, since=с),
    }
    if full:
        сводка["treasury"] = await _treasury(session, city, since=с)
    return сводка


async def _market(session: AsyncSession, узлы: list[uuid.UUID], *, since: datetime) -> dict:
    """Оборот, число сделок и медианная цена по товарам своего города (D-003)."""
    if not узлы:
        return {"trades": 0, "volume": 0.0, "prices": {}}
    rows = (
        await session.execute(
            select(Trade.type_key, Trade.price, Trade.amount).where(
                Trade.node_id.in_(узлы), Trade.at >= since
            )
        )
    ).all()
    по_товарам: dict[str, list[int]] = {}
    оборот = 0
    for имя, цена, сколько in rows:
        по_товарам.setdefault(имя, []).append(int(цена))
        оборот += int(цена) * int(сколько)
    return {
        "trades": len(rows),
        "volume": оборот / MONEY_SCALE / AMOUNT_SCALE,
        "prices": {имя: median(цены) / MONEY_SCALE for имя, цены in по_товарам.items()},
    }


async def _people(session: AsyncSession, узлы: list[uuid.UUID], *, since: datetime) -> dict:
    """Сколько людей в городе сейчас и сколько печаталось здесь за окно.

    «Миграция — самый честный отзыв о власти» (D-140). Гражданства в движке
    ещё нет, поэтому считается присутствие, а не подданство, — и это названо
    своим именем, а не выдано за перепись.
    """
    if not узлы:
        return {"here": 0, "printed": 0}
    здесь = await session.scalar(
        select(func.count())
        .select_from(Body)
        .where(Body.node_id.in_(узлы), Body.state == BodyState.ALIVE)
    )
    печаталось = await session.scalar(
        select(func.count())
        .select_from(Event)
        .where(
            Event.kind == EventKind.BODY_PRINTED.value,
            Event.node_id.in_(узлы),
            Event.at >= since,
        )
    )
    return {"here": int(здесь or 0), "printed": int(печаталось or 0)}


async def _production(
    session: AsyncSession, узлы: list[uuid.UUID], *, since: datetime
) -> dict:
    """Что город произвёл за окно: добыто, убрано, выпущено станками."""
    if not узлы:
        return {"mined": {}, "harvested": 0.0, "crafted": {}}
    события = (
        await session.execute(
            select(Event).where(
                Event.node_id.in_(узлы),
                Event.at >= since,
                Event.kind.in_(
                    (
                        EventKind.MINING_SWING.value,
                        EventKind.PLOT_HARVESTED.value,
                        EventKind.CRAFT_FINISHED.value,
                    )
                ),
            )
        )
    ).scalars().all()

    добыто: dict[str, float] = {}
    сделано: dict[str, float] = {}
    убрано = 0.0
    for событие in события:
        груз = событие.payload or {}
        if событие.kind == EventKind.MINING_SWING.value:
            #: Порода в событии удара не названа: она известна жиле. Считаем
            #: единицами — панели важен объём, а не сорт (сорт есть у рынка).
            добыто["всего"] = добыто.get("всего", 0.0) + float(груз.get("mined", 0))
        elif событие.kind == EventKind.PLOT_HARVESTED.value:
            убрано += float(груз.get("harvested", 0) or 0)
        else:
            имя = str(груз.get("output") or "?")
            сделано[имя] = сделано.get(имя, 0.0) + float(груз.get("units", 0) or 0)
    return {"mined": добыто, "harvested": убрано, "crafted": сделано}


async def _energy(
    session: AsyncSession, constants: Constants, city: City, *, since: datetime
) -> dict:
    """Пул, отпуск по счётчику и на работу. Выработка идёт временем (D-082)."""
    from src.models.energy import EnergyPool

    #: Пул заведён **на узле-представителе** города, а `energy.pool_of` ищет его
    #: от узла застройки: представителю он ответил бы «сети нет». Берём напрямую.
    pool = (
        await session.execute(
            select(EnergyPool).where(EnergyPool.node_id == city.node_id)
        )
    ).scalar_one_or_none()

    события = (
        await session.execute(
            select(Event).where(
                Event.at >= since,
                Event.kind.in_(
                    (EventKind.ENERGY_DRAWN.value, EventKind.UTILITY_METERED.value)
                ),
            )
        )
    ).scalars().all()
    на_работу = 0.0
    на_быт = 0.0
    for событие in события:
        груз = событие.payload or {}
        сколько = float(груз.get("energy", 0) or 0)
        if событие.kind == EventKind.ENERGY_DRAWN.value:
            на_работу += сколько
        else:
            на_быт += сколько
    return {
        "stored": 0.0 if pool is None else float(pool.stored),
        "tariff": 0.0 if pool is None else float(pool.tariff),
        "spent_work": на_работу,
        "spent_home": на_быт,
    }


async def _goods(session: AsyncSession, узлы: list[uuid.UUID], *, since: datetime) -> dict:
    """Баланс товара: сколько лежит в городе сейчас.

    Ввоз и вывоз считает таможня и отдаёт разделом `trade` (D-123): здесь —
    остаток, то есть то, что в городе есть прямо сейчас.
    """
    if not узлы:
        return {}
    from src.engine import world

    остатки: dict[str, float] = {}
    for node_id in узлы:
        node = await session.get(Node, node_id)
        if node is None:  # pragma: no cover
            continue
        двор = await world.node_container(session, node)
        rows = (
            await session.execute(
                select(Item.type_key, func.sum(Item.amount))
                .where(Item.container_id == двор.id)
                .group_by(Item.type_key)
            )
        ).all()
        for имя, сколько in rows:
            остатки[имя] = остатки.get(имя, 0.0) + int(сколько or 0) / AMOUNT_SCALE
    return остатки


async def _trade(
    session: AsyncSession, constants: Constants, city: City, *, since: datetime
) -> dict:
    """Ввезено, вывезено, ходок и уплачено пошлин (D-123, D-124)."""
    from src.engine import customs

    return await customs.traffic(session, constants, city, since=since)


async def _treasury(session: AsyncSession, city: City, *, since: datetime) -> dict:
    """Казна по основаниям: собрано и потрачено за окно, плюс остаток.

    Поимённого здесь нет: кто именно заплатил пошлину, видно тому, кому устав
    показывает казну (`treasury_publicity`), и это отдельная механика (D-124).
    """
    from src.engine import city as town

    счёт = await town.treasury(session, city)
    rows = (
        await session.execute(
            select(LedgerTransaction.reason, func.sum(LedgerEntry.amount))
            .select_from(LedgerEntry)
            .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
            .where(LedgerEntry.account_id == счёт.id, LedgerTransaction.at >= since)
            .group_by(LedgerTransaction.reason)
        )
    ).all()

    собрано: dict[str, float] = {}
    потрачено: dict[str, float] = {}
    for основание, сумма in rows:
        значение = int(сумма or 0)
        имя = основание.value if hasattr(основание, "value") else str(основание)
        if значение >= 0:
            собрано[имя] = собрано.get(имя, 0.0) + значение / MONEY_SCALE
        else:
            потрачено[имя] = потрачено.get(имя, 0.0) - значение / MONEY_SCALE
    return {
        "balance": await town.treasury_balance(session, city) / MONEY_SCALE,
        "collected": собрано,
        "spent": потрачено,
    }


async def store_daily(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> int:
    """Сложить срез каждого города в суточные метрики. Возвращает число городов.

    История нужна, чтобы отличать всплеск от тенденции: решение по всплеску —
    худший вид управления (D-140). Глубина — `trade.report_retention` суток,
    и её держит та же таблица, что и метрики мира: формула одна на панель,
    дашборд и проверку инвариантов (D-139).
    """
    from src.telemetry.metrics import remember

    moment = now or datetime.now(UTC)
    города = (await session.execute(select(City))).scalars().all()
    for city in города:
        срез = await collect(session, constants, city, full=True, now=moment)
        рынок = срез["market"]
        await remember(
            session,
            {
                f"city.{city.id}.trades": float(рынок["trades"]),
                f"city.{city.id}.volume": float(рынок["volume"]),
                f"city.{city.id}.people": float(срез["people"]["here"]),
                f"city.{city.id}.treasury": float(срез["treasury"]["balance"]),
                f"city.{city.id}.energy": float(срез["energy"]["stored"]),
            },
            now=moment,
        )
    return len(города)


__all__ = ["blind", "city_nodes", "collect", "store_daily"]
