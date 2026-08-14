"""Недвижимость: выкуп участка, ценная бумага, здание (D-089, D-106, D-116, D-125).

## Выкуп городской земли

Пустой городской узел вправе купить **любой** игрок — землю больше не только
раздаёт власть. Цену за квадратный метр назначает государство код-законом
`land_price`; с каждым кольцом от биопринтера — центра города — участок
дешевеет на `land.price_decay_per_ring`. Удалённость меряется по графу: шагами
от узла с биопринтером, а не свойством, записанным при генерации. Выручка идёт
в казну города: город продаёт свою землю, а не движок.

## Ценная бумага

Владение оформляется бумагой (`models/estate.Deed`) — электронным документом.
Бумага живёт в Сети: гибель тела её не трогает, распоряжаются ею удалённо, как
счётом и ордерами. Продажа — договор купли-продажи: владелец выставляет цену
(всем либо адресно), покупатель платит — и бумага вместе с участком переходит
к нему одной транзакцией. Эскроу не нужен: и деньги, и титул меняют владельца
в один момент.

## Здание

На пустом участке сначала строят здание, и только в здании ставят станки:
станок занимает `build.slots_per_area` квадратных метров, так что площадь дома
— это его вместимость, а не украшение (D-106). Стройка — работа: материалы
первой ступени прочности (`build.materials_per_m2`) списываются сразу, здание
встаёт по сроку `build.labor_per_m2` часов на метр — заданием журнала, как
всякое длительное дело.
"""

from __future__ import annotations

import uuid
from collections import deque
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, ledger, travel
from src.engine.jobs import enqueue, handler
from src.models.city import City
from src.models.estate import Building, Deed
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Identity
from src.models.inventory import Item
from src.models.job import Job, JobKind
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Edge, Node
from src.units import MINUTES_PER_HOUR, PERCENT, money


class EstateError(Exception):
    pass


class NotForSale(EstateError):
    """Эта земля не продаётся: она либо занята, либо не городская."""


class NotEnoughMoney(EstateError):
    pass


class NotOwner(EstateError):
    """Землёй распоряжается хозяин, а городской — власть с правом `land`."""


class BadName(EstateError):
    """Имя пустое либо длиннее разумного. Табличка — не письмо."""


class NoBuilding(EstateError):
    """Здания на участке нет: сначала строят, потом ставят станки (D-106)."""


class NoRoom(EstateError):
    """В здании нет места: станки занимают площадь, и она кончилась."""


# --- цена земли (D-089) ------------------------------------------------------


async def rings_from_center(session: AsyncSession, node: Node) -> int:
    """Удалённость участка от центра города — шагами графа от биопринтера.

    Центр — узел, где стоит биопринтер (у столицы — Принтер Предтеч); город
    растёт кольцами вокруг него, и ценность земли падает с каждым кольцом.
    Меряем по рёбрам, а не по свойству «кольцо»: свойство — запись при
    генерации, а рёбра — то, как по городу реально ходят.
    """
    from src.engine import world

    центр = await world.spawn_point(session)
    if центр is None or центр.id == node.id:
        return 0

    #: Поиск в ширину по рёбрам. Граф мал; станет велик — появится повод
    #: считать заранее, а не на каждый запрос.
    рёбра = (await session.execute(select(Edge))).scalars().all()
    соседи: dict[uuid.UUID, list[uuid.UUID]] = {}
    for ребро in рёбра:
        соседи.setdefault(ребро.node_a_id, []).append(ребро.node_b_id)
        соседи.setdefault(ребро.node_b_id, []).append(ребро.node_a_id)

    видели = {центр.id}
    очередь: deque[tuple[uuid.UUID, int]] = deque([(центр.id, 0)])
    while очередь:
        узел, шагов = очередь.popleft()
        if узел == node.id:
            return шагов
        for сосед in соседи.get(узел, ()):  # noqa: B007
            if сосед not in видели:
                видели.add(сосед)
                очередь.append((сосед, шагов + 1))
    #: До узла нет дороги — земля на отшибе, дальше самого дальнего кольца.
    return len(видели)


async def price_of(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    city: City,
    node: Node,
) -> int:
    """Цена участка минорными единицами: ставка города × спад × площадь.

    Ставку у центра назначает город код-законом `land_price` (ТК/м²); с каждым
    кольцом от биопринтера цена падает на `land.price_decay_per_ring`.
    """
    from src.engine import city as town

    ставка = town.law_number(constants, catalog, city, "land_price")
    if ставка <= 0:
        raise NotForSale("город не назначил цену земли: код-закон `land_price` пуст")
    спад = 1 - constants[R.LAND_PRICE_DECAY_PER_RING] / PERCENT
    колец = await rings_from_center(session, node)
    за_метр = ставка * (спад ** колец)
    return max(1, money(за_метр * float(node.area_m2)))


async def is_vacant(session: AsyncSession, constants: Constants, node: Node) -> bool:
    """Пустой ли узел: продаётся только земля без ничего.

    Городская застройка (кузница, рынок, администрация) и узлы с жилой этой
    кнопкой не продаются: это не «пустой участок», а работающее имущество
    города, и распоряжаться им — дело власти, а не прейскуранта.
    """
    if await built_area(session, node) > 0:
        return False
    #: Транзитные ворота города — общая дорога, а не участок (D-176).
    if (node.properties or {}).get("выход"):
        return False
    _, занято = await slots(session, constants, node)
    if занято > 0:
        return False
    from src.models.world import Vein

    жила = await session.scalar(
        select(Vein.id).where(Vein.node_id == node.id).limit(1)
    )
    return жила is None


async def buy(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    node: Node,
) -> Deed:
    """Выкупить пустой городской участок. Присутственно: землю смотрят ногами.

    Деньги уходят в казну города, покупателю выдаётся ценная бумага. Дикую
    землю не покупают — её занимают (`world.claim_node`): вне города некому
    назначить цену и некуда платить.
    """
    from src.engine import city as town

    if body.state is not BodyState.ALIVE:
        raise EstateError("мёртвое тело не покупает")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError("участок покупают ногами: дойдите до него")
    if node.owner_identity_id is not None:
        raise NotForSale("участок уже за кем-то")
    if node.owner_city_id is None:
        raise NotForSale("это не городская земля: дикую занимают, а не покупают")
    if not await is_vacant(session, constants, node):
        raise NotForSale(
            "узел не пустой: застройку и жилы города прейскурант не продаёт"
        )

    город = await town.by_id(session, node.owner_city_id)
    if город is None:  # pragma: no cover — городская земля без города это баг
        raise NotForSale("узел приписан к несуществующему городу")

    #: Кто вправе занимать участки в кольцах, отвечает код-закон `build_permit`
    #: (D-089). По умолчанию — граждане, и до D-160 это читалось как «все».
    if not town.may_take_city_land(
        catalog, город, await town.is_citizen(session, body.identity_id, город)
    ):
        raise NotForSale(
            f"«{город.name}» продаёт землю не всякому: код-закон build_permit — "
            f"«{town.law(catalog, город, 'build_permit')}». Вступите в граждане"
        )

    цена = await price_of(session, constants, catalog, город, node)
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, body.identity_id)
    остаток = await ledger.balance(session, счёт.id)
    if остаток < цена:
        raise NotEnoughMoney(
            f"участок стоит {цена} минорных единиц, а на счету {остаток}"
        )

    казна = await town.treasury(session, город)
    await ledger.transfer(
        session,
        PostingReason.TRADE,
        debit=счёт.id,
        credit=казна.id,
        amount=цена,
        memo={"выкуп участка": node.key, "город": город.name},
    )

    node.owner_identity_id = body.identity_id
    deed = await issue_deed(session, node, body.identity_id, paid=цена)

    await events.record(
        session,
        EventKind.LAND_BOUGHT,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        city_id=str(город.id),
        price=цена,
        deed_id=str(deed.id),
    )
    return deed


async def may_name(session: AsyncSession, body: Body, node: Node) -> bool:
    """Вправе ли это тело дать узлу имя (D-178).

    Своей землёй распоряжается хозяин, городской — власть с правом `land`: тем
    же, которым она эту землю раздаёт (D-089). Ничья земля имени не носит.
    """
    from src.engine import city as town
    from src.models.city import Power

    if node.owner_identity_id is not None:
        return node.owner_identity_id == body.identity_id
    if node.owner_city_id is None:
        return False
    город = await town.by_id(session, node.owner_city_id)
    return город is not None and await town.may(
        session, body.identity_id, город, Power.LAND
    )


async def rename(
    session: AsyncSession, body: Body, node: Node, name: str
) -> Node:
    """Дать участку имя. Табличку прибивают на месте, а не из Сети (D-178).

    Меняется подпись, а не ключ узла: на `terra.capital.lot2` ссылаются бумаги,
    рёбра и события, и переименование не вправе их порвать.
    """
    from src.runtime import LAND_NAME_LIMIT

    if body.state is not BodyState.ALIVE:
        raise EstateError("мёртвое тело ничего не переименовывает")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError("до участка надо дойти: табличку прибивают на месте")
    if not await may_name(session, body, node):
        raise NotOwner(
            "участок не ваш: имя даёт хозяин, а городской земле — власть с "
            "правом на участки"
        )

    название = name.strip()
    if not название:
        raise BadName("у участка должно быть имя")
    if len(название) > LAND_NAME_LIMIT:
        raise BadName(f"имя длиннее {LAND_NAME_LIMIT} знаков")

    было, node.name = node.name, название
    await session.flush()
    await events.record(
        session,
        EventKind.LAND_RENAMED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        was=было,
        now=название,
    )
    return node


# --- ценная бумага (D-116) ---------------------------------------------------


async def issue_deed(
    session: AsyncSession, node: Node, owner_id: uuid.UUID, *, paid: int = 0
) -> Deed:
    """Выдать бумагу на участок. Одна на узел: повторная выдача переписывает
    владельца — это смена титула, а не вторая бумага."""
    существующая = (
        await session.execute(select(Deed).where(Deed.node_id == node.id))
    ).scalar_one_or_none()
    if существующая is not None:
        существующая.owner_identity_id = owner_id
        существующая.sale_price = None
        существующая.sale_to_identity_id = None
        await session.flush()
        return существующая

    deed = Deed(node_id=node.id, owner_identity_id=owner_id, paid=paid)
    session.add(deed)
    await session.flush()
    await events.record(
        session,
        EventKind.DEED_ISSUED,
        actor_identity_id=owner_id,
        node_id=node.id,
        deed_id=str(deed.id),
        paid=paid,
    )
    return deed


async def offer_deed(
    session: AsyncSession,
    identity: Identity,
    deed: Deed,
    price: int,
    *,
    to: Identity | None = None,
) -> Deed:
    """Выставить бумагу на продажу: всем либо адресно. Удалённое действие.

    Ноль ценой снимает бумагу с продажи.
    """
    if deed.owner_identity_id != identity.id:
        raise EstateError("бумага не ваша: продают своё")
    if price <= 0:
        deed.sale_price = None
        deed.sale_to_identity_id = None
    else:
        deed.sale_price = price
        deed.sale_to_identity_id = None if to is None else to.id
    await session.flush()

    await events.record(
        session,
        EventKind.DEED_OFFERED,
        actor_identity_id=identity.id,
        node_id=deed.node_id,
        deed_id=str(deed.id),
        price=deed.sale_price,
        to=None if to is None else to.name,
    )
    return deed


async def buy_deed(
    session: AsyncSession, buyer: Identity, deed: Deed
) -> Deed:
    """Купить выставленную бумагу: деньги продавцу, титул покупателю.

    Договор купли-продажи одной транзакцией: эскроу не нужен, потому что и
    деньги, и бумага меняют владельца в один момент. Удалённое действие —
    документы живут в Сети.
    """
    if deed.sale_price is None:
        raise NotForSale("бумага не выставлена на продажу")
    if deed.owner_identity_id == buyer.id:
        raise EstateError("своя бумага не покупается")
    if deed.sale_to_identity_id is not None and deed.sale_to_identity_id != buyer.id:
        raise NotForSale("договор адресный: бумага обещана другому")

    цена = int(deed.sale_price)
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, buyer.id)
    остаток = await ledger.balance(session, счёт.id)
    if остаток < цена:
        raise NotEnoughMoney(f"бумага стоит {цена}, а на счету {остаток}")

    продавец = await ledger.account_for(
        session, AccountKind.IDENTITY, deed.owner_identity_id
    )
    await ledger.transfer(
        session,
        PostingReason.TRADE,
        debit=счёт.id,
        credit=продавец.id,
        amount=цена,
        memo={"договор купли-продажи": str(deed.id)},
    )

    прежний = deed.owner_identity_id
    deed.owner_identity_id = buyer.id
    deed.sale_price = None
    deed.sale_to_identity_id = None

    #: Титул и есть владение: узел переходит вместе с бумагой.
    node = await session.get(Node, deed.node_id)
    if node is not None:
        node.owner_identity_id = buyer.id
    await session.flush()

    await events.record(
        session,
        EventKind.DEED_SOLD,
        actor_identity_id=buyer.id,
        node_id=deed.node_id,
        deed_id=str(deed.id),
        price=цена,
        seller=str(прежний),
    )
    return deed


async def deeds_of(session: AsyncSession, identity_id: uuid.UUID) -> list[Deed]:
    return list(
        (
            await session.execute(
                select(Deed).where(Deed.owner_identity_id == identity_id)
            )
        ).scalars().all()
    )


async def deeds_on_sale(session: AsyncSession, identity_id: uuid.UUID) -> list[Deed]:
    """Бумаги, которые этой личности можно купить: открытые и адресованные ей."""
    rows = (
        await session.execute(
            select(Deed).where(
                Deed.sale_price.is_not(None),
                Deed.owner_identity_id != identity_id,
            )
        )
    ).scalars().all()
    return [
        deed
        for deed in rows
        if deed.sale_to_identity_id is None or deed.sale_to_identity_id == identity_id
    ]


# --- здание (D-106, D-125) ---------------------------------------------------


async def buildings_of(session: AsyncSession, node: Node) -> list[Building]:
    return list(
        (
            await session.execute(select(Building).where(Building.node_id == node.id))
        ).scalars().all()
    )


async def built_area(session: AsyncSession, node: Node) -> float:
    total = await session.scalar(
        select(func.coalesce(func.sum(Building.area_m2), 0)).where(
            Building.node_id == node.id
        )
    )
    return float(total or 0)


async def slots(
    session: AsyncSession, constants: Constants, node: Node
) -> tuple[int, int]:
    """Вместимость и занятость: (мест всего, мест занято).

    Место — `build.slots_per_area` квадратных метров здания; занимают его
    станки и мебель, стоящие в узле.
    """
    from src.constants import current_catalog
    from src.constants.catalog import ItemKind
    from src.engine import world

    площадь = await built_area(session, node)
    всего = int(площадь // constants[R.BUILD_SLOTS_PER_AREA])

    книга = current_catalog().recipes
    двор = await world.node_container(session, node)
    вещи = (
        await session.execute(select(Item).where(Item.container_id == двор.id))
    ).scalars().all()
    занято = 0
    for вещь in вещи:
        try:
            рецепт = книга.recipe(вещь.type_key)
        except Exception:  # noqa: BLE001 — сырьё у станка рецептом не описано
            continue
        if рецепт.kind in (ItemKind.STATION, ItemKind.FURNITURE):
            занято += 1
    return всего, занято


async def construct(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    node: Node,
    area: float,
    *,
    now: datetime | None = None,
) -> Job:
    """Построить здание на своём участке. Материалы сразу, здание — по сроку.

    Первая ступень прочности: дерево и верёвка (`build.materials_per_m2`).
    Городская земля строится городом — здесь только своя (D-089).
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise EstateError("мёртвое тело не строит")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError("строят ногами: дойдите до участка")
    if node.owner_identity_id != body.identity_id:
        raise EstateError("участок не ваш: строят у себя")
    if area <= 0:
        raise EstateError("здание нулевой площади — это двор, он уже есть")

    занято = await built_area(session, node)
    if занято + area > float(node.area_m2):
        raise NoRoom(
            f"на участке {float(node.area_m2):.0f} м², застроено {занято:.0f}: "
            f"ещё {area:.0f} не помещается"
        )

    #: Материалы — из вольта, на метр застройки. Списываются сразу: стройка
    #: началась, и брус уже в стене, а не в мешке.
    from src.engine import craft, world

    нормы = constants[R.BUILD_MATERIALS_PER_M2]
    нужно = {имя: float(сколько) * area for имя, сколько in нормы.items()}
    карман = await world.body_container(session, body)
    запас = await craft._stock(session, карман, tuple(нужно))  # noqa: SLF001
    for pick in craft._pick(запас, нужно):  # noqa: SLF001
        if pick.item.amount > pick.take:
            pick.item.amount -= pick.take
        else:
            await session.delete(pick.item)
    await session.flush()

    минут = area * constants[R.BUILD_LABOR_PER_M2] * MINUTES_PER_HOUR
    срок = moment + timedelta(minutes=минут)
    event = await events.record(
        session,
        EventKind.CRAFT_STARTED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        work="build",
        area=area,
        spent=нужно,
        ready_at=срок.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.BUILD_FINISH,
        срок,
        payload={"node": str(node.id), "area": area, "identity": str(body.identity_id)},
        dedup_key=f"build:{node.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover — ключ уникален по событию
        raise EstateError("стройка уже поставлена")
    return job


@handler(JobKind.BUILD_FINISH)
async def finish_build(session: AsyncSession, job: Job) -> None:
    """Стройка окончена: здание встало на участок."""
    node = await session.get(Node, uuid.UUID(job.payload["node"]))
    if node is None:  # pragma: no cover
        raise EstateError(f"стройка {job.id} ссылается в никуда")
    building = Building(node_id=node.id, area_m2=float(job.payload["area"]))
    session.add(building)
    await session.flush()

    await events.record(
        session,
        EventKind.BUILDING_BUILT,
        actor_identity_id=uuid.UUID(job.payload["identity"]),
        node_id=node.id,
        building_id=str(building.id),
        area=float(job.payload["area"]),
    )
