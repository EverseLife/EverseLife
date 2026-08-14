"""Таможня: ставка, норма и запрет на границе города (D-123).

Настоящая задача власти звучит так: «наши фермеры не выдерживают дешёвого
привозного хлеба» и «наши цеха стоят без руды, потому что её всю вывезли».
Шлагбаумом это не решается — нужны ставка, порог и разные ответы на разные
беды. Отсюда четыре код-закона, по два на направление:

    import_duty / export_duty   ставка % и беспошлинная норма кг
    import_ban  / export_ban    список товаров, которые не проходят вовсе

## Норма делает пошлину прицельной

Ставка без порога бьёт по всем одинаково, и первым страдает новичок, привёзший
мешок репы себе на ужин. Беспошлинная норма отделяет **бытовой провоз от
промысла**: провёз меньше нормы за `trade.duty_free_window` — не заплатил
ничего; провёз десять норм — заплатил за всё, что сверх. Норма считается на
человека и по журналу переходов, а не по одной ходке: иначе её обходят, разбив
груз на десять заходов.

## Как считается стоимость

Пошлина — доля от **справочной цены городского стакана**: медианы сделок за
`trade.reference_price_window`. **Нет сделок — нет оценки, и пошлина не
берётся.** Город, у которого рынок пуст, не может обложить то, чему сам не
знает цены: сначала рынок, потом таможня.

## Где проходит граница

Между городами и «ничьей» землёй. Переход считается пересечением, если город
на входе и на выходе разный: шаг внутри своего города таможни не знает, выход
за стену — знает. Списывается **при выходе в дорогу**, когда обе стороны уже
известны: платить на приходе значило бы пускать в город то, за что заплатить
нечем.

**Нечем платить — товар не проходит, долга не возникает** (D-123). Движок
отказывает в переходе целиком: решение, что бросить, принимает человек, а не
таможня за него.

## Формат закона

Значение код-закона читается двумя способами, и оба честные:

* число — ставка на **всё**, нормы нет: «десять процентов со всего, что
  пересекает границу»;
* карта `{товар: {"rate": %, "free": кг}}` — прицельно по товарам.

Ветвлений по названию закона в движке нет: `import_duty` и `export_duty`
разбираются одним кодом, отличается только направление.

## Чем платим

Норма на человека дробится на подставных перевозчиков, и полностью это не
лечится. Барьером остаётся то же, что везде: каждый мул — живой человек,
тратящий своё время на переход. Контрабанда **должна** быть возможной, иначе
таможня перестаёт быть политикой и становится физикой (D-123).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events
from src.models.city import City
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.market import Trade
from src.models.world import Node
from src.telemetry.metrics import median
from src.units import MONEY_SCALE, PERCENT, amount_float, money_str

#: Направления. Имя закона собирается из направления, а не выбирается ветвлением.
IMPORT = "import"
EXPORT = "export"


class CustomsError(Exception):
    pass


class Banned(CustomsError):
    """Запрещённое к провозу. Крайняя мера города, и она абсолютна."""


class CannotPay(CustomsError):
    """Нечем платить пошлину. Товар не проходит, долга не возникает (D-123)."""


@dataclass(frozen=True, slots=True)
class Charge:
    """Что насчитала таможня по одному направлению."""

    city: City | None
    direction: str
    duty: int = 0
    #: Товар → сколько килограммов сверх нормы обложено.
    taxed: dict[str, float] = field(default_factory=dict)
    #: Товар → сколько килограммов прошло вообще: это и есть строка сводки.
    moved: dict[str, float] = field(default_factory=dict)


def _law(catalog: Catalog, city: City, direction: str, kind: str) -> object:
    from src.engine import city as town

    return town.law(catalog, city, f"{direction}_{kind}")


def banned(catalog: Catalog, city: City, direction: str) -> set[str]:
    """Что этот город не пропускает в этом направлении."""
    raw = _law(catalog, city, direction, "ban")
    if raw is None:
        return set()
    if isinstance(raw, (list, tuple)):
        return {str(имя).strip() for имя in raw if str(имя).strip()}
    text = str(raw).strip()
    if text.lower() in ("", "пусто", "нет", "-"):
        return set()
    return {кусок.strip() for кусок in text.split(",") if кусок.strip()}


def rates(catalog: Catalog, city: City, direction: str) -> dict[str, dict[str, float]]:
    """Ставка и норма по товарам. Пустая карта — пошлины нет.

    Ключ `*` означает «на всё»: так записывается ставка без разбора товаров.
    """
    raw = _law(catalog, city, direction, "duty")
    if raw is None:
        return {}
    if isinstance(raw, dict):
        out: dict[str, dict[str, float]] = {}
        for товар, условие in raw.items():
            if isinstance(условие, dict):
                ставка = float(условие.get("rate", 0) or 0)
                норма = float(условие.get("free", 0) or 0)
            else:
                ставка, норма = float(условие or 0), 0.0
            if ставка > 0:
                out[str(товар)] = {"rate": ставка, "free": норма}
        return out
    try:
        ставка = float(str(raw).strip())
    except ValueError:
        return {}
    return {"*": {"rate": ставка, "free": 0.0}} if ставка > 0 else {}


async def reference_price(
    session: AsyncSession,
    constants: Constants,
    city: City,
    type_key: str,
    *,
    now: datetime | None = None,
) -> float | None:
    """Справочная цена городского стакана: медиана сделок за окно (D-123).

    `None` — сделок не было. Это не ноль: с неизвестной цены пошлину не берут.
    """
    from src.engine import panel

    moment = now or datetime.now(UTC)
    окно = timedelta(hours=constants[R.TRADE_REFERENCE_PRICE_WINDOW])
    узлы = [узел.id for узел in await panel.city_nodes(session, city)]
    if not узлы:
        return None
    цены = (
        await session.execute(
            select(Trade.price).where(
                Trade.node_id.in_(узлы),
                Trade.type_key == type_key,
                Trade.at >= moment - окно,
            )
        )
    ).scalars().all()
    if not цены:
        return None
    return median([int(цена) for цена in цены]) / MONEY_SCALE


async def moved_in_window(
    session: AsyncSession,
    constants: Constants,
    identity_id: uuid.UUID,
    city: City,
    direction: str,
    type_key: str,
    *,
    now: datetime | None = None,
) -> float:
    """Сколько килограммов этого товара человек уже провёз за окно нормы.

    Считается по журналу переходов, а не по одной ходке: норма, которую можно
    обнулить, разбив груз на десять заходов, не норма (D-123).
    """
    moment = now or datetime.now(UTC)
    окно = timedelta(hours=constants[R.TRADE_DUTY_FREE_WINDOW])
    строки = (
        await session.execute(
            select(Event).where(
                Event.kind == EventKind.CUSTOMS_CROSSED.value,
                Event.actor_identity_id == identity_id,
                Event.at >= moment - окно,
            )
        )
    ).scalars().all()
    всего = 0.0
    for строка in строки:
        груз = строка.payload or {}
        if груз.get("city") != str(city.id) or груз.get("direction") != direction:
            continue
        всего += float((груз.get("moved") or {}).get(type_key, 0) or 0)
    return всего


async def assess(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    city: City,
    direction: str,
    *,
    now: datetime | None = None,
) -> Charge:
    """Посчитать пошлину за то, что тело несёт. Ничего не списывает.

    Прогноз и списание считаются одним кодом по той же причине, что и в крафте:
    разошедшийся прогноз хуже отсутствующего (D-092).
    """
    from src.engine import gear, world

    moment = now or datetime.now(UTC)
    запрет = banned(catalog, city, direction)
    ставки = rates(catalog, city, direction)

    карман = await world.body_container(session, body)
    вещи = (
        await session.execute(select(Item).where(Item.container_id == карман.id))
    ).scalars().all()

    пошлина = 0.0
    обложено: dict[str, float] = {}
    провезено: dict[str, float] = {}
    for вещь in вещи:
        сколько = amount_float(вещь.amount)
        килограммов = gear.mass_of(catalog, вещь.type_key, сколько)
        провезено[вещь.type_key] = провезено.get(вещь.type_key, 0.0) + килограммов
        if вещь.type_key in запрет:
            raise Banned(
                f"«{вещь.type_key}» не проходит границу города «{city.name}»: "
                f"{'ввоз' if direction == IMPORT else 'вывоз'} запрещён"
            )

        условие = ставки.get(вещь.type_key) or ставки.get("*")
        if условие is None or килограммов <= 0:
            continue
        цена = await reference_price(
            session, constants, city, вещь.type_key, now=moment
        )
        if цена is None:
            #: Нет сделок — нет оценки. Сначала рынок, потом таможня (D-123).
            continue

        уже = await moved_in_window(
            session, constants, body.identity_id, city, direction, вещь.type_key,
            now=moment,
        )
        норма = условие["free"]
        сверх_кг = max(0.0, килограммов - max(0.0, норма - уже))
        if сверх_кг <= 0:
            continue
        #: Стоимость обложенного считается по цене **единицы**, а норма — в
        #: килограммах: вольт задаёт их именно так, и переводить одно в другое
        #: надо через массу, а не подменять единицы (D-123, D-146).
        за_килограмм = цена / (килограммов / сколько) if сколько > 0 else 0.0
        пошлина += сверх_кг * за_килограмм * условие["rate"] / PERCENT
        обложено[вещь.type_key] = обложено.get(вещь.type_key, 0.0) + сверх_кг

    return Charge(
        city=city,
        direction=direction,
        duty=int(round(пошлина * MONEY_SCALE)),
        taxed=обложено,
        moved=провезено,
    )


async def cross(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    from_node: Node,
    to_node: Node,
    *,
    now: datetime | None = None,
) -> list[Charge]:
    """Провести тело через границу: списать пошлины и записать провоз.

    Возвращает начисления по направлениям. Отказ — исключение: таможня не
    решает за человека, что бросить, она только не пускает.
    """
    from src.engine import city as town
    from src.engine import ledger

    moment = now or datetime.now(UTC)
    откуда = await town.of_node(session, from_node)
    куда = await town.of_node(session, to_node)
    if (откуда is None and куда is None) or (
        откуда is not None and куда is not None and откуда.id == куда.id
    ):
        #: Шаг внутри своего города таможни не знает.
        return []

    начисления: list[Charge] = []
    if откуда is not None:
        начисления.append(
            await assess(session, constants, catalog, body, откуда, EXPORT, now=moment)
        )
    if куда is not None:
        начисления.append(
            await assess(session, constants, catalog, body, куда, IMPORT, now=moment)
        )

    всего = sum(начисление.duty for начисление in начисления)
    if всего > 0:
        счёт = await ledger.account_for(
            session, AccountKind.IDENTITY, body.identity_id
        )
        остаток = await ledger.balance(session, счёт.id)
        if остаток < всего:
            await events.record(
                session,
                EventKind.CUSTOMS_REFUSED,
                actor_identity_id=body.identity_id,
                node_id=from_node.id,
                duty=всего,
                short=всего - остаток,
            )
            raise CannotPay(
                f"пошлина {money_str(всего)} ₭, а на счету {money_str(остаток)} ₭: "
                "товар не проходит. Долга при этом не возникает"
            )

    for начисление in начисления:
        if начисление.city is None:  # pragma: no cover — город есть по построению
            continue
        if начисление.duty > 0:
            счёт = await ledger.account_for(
                session, AccountKind.IDENTITY, body.identity_id
            )
            казна = await town.treasury(session, начисление.city)
            await ledger.transfer(
                session,
                PostingReason.DUTY,
                debit=счёт.id,
                credit=казна.id,
                amount=начисление.duty,
                memo={
                    "таможня": начисление.city.name,
                    "направление": начисление.direction,
                    "обложено": начисление.taxed,
                },
            )
        await events.record(
            session,
            EventKind.CUSTOMS_CROSSED,
            actor_identity_id=body.identity_id,
            node_id=from_node.id,
            city=str(начисление.city.id),
            direction=начисление.direction,
            duty=начисление.duty,
            moved=начисление.moved,
            taxed=начисление.taxed,
        )
    return начисления


async def traffic(
    session: AsyncSession,
    constants: Constants,
    city: City,
    *,
    since: datetime,
) -> dict:
    """Ввоз, вывоз и собранная пошлина за период — строка сводки (D-124).

    «Ввезено и вывезено по товарам, в весе и в ходках» — прямое основание для
    ставки: видно, что хлеб идёт извне потоком.
    """
    строки = (
        await session.execute(
            select(Event).where(
                Event.kind == EventKind.CUSTOMS_CROSSED.value, Event.at >= since
            )
        )
    ).scalars().all()

    ввоз: dict[str, float] = {}
    вывоз: dict[str, float] = {}
    ходок = {IMPORT: 0, EXPORT: 0}
    собрано = 0
    for строка in строки:
        груз = строка.payload or {}
        if груз.get("city") != str(city.id):
            continue
        куда = ввоз if груз.get("direction") == IMPORT else вывоз
        ходок[str(груз.get("direction"))] = ходок.get(str(груз.get("direction")), 0) + 1
        for товар, килограммов in (груз.get("moved") or {}).items():
            куда[товар] = куда.get(товар, 0.0) + float(килограммов or 0)
        собрано += int(груз.get("duty", 0) or 0)
    return {
        "imported": ввоз,
        "exported": вывоз,
        "trips_in": ходок.get(IMPORT, 0),
        "trips_out": ходок.get(EXPORT, 0),
        "duty_collected": собрано / MONEY_SCALE,
    }
