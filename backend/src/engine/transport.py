"""Транспорт: как возят то, что не унести в руках (D-107, D-129, D-157).

У груза появилась масса (D-146), и предел носимого заработал: в руках
`inventory.carry_mass`, всё сверх — «только транспортом». Транспорта при этом
не было, и хуже того — повозка тяжелее самого предела, то есть в руки не
берётся вовсе. Профессия возчика, ради которой писалась вся логистика, не
существовала ни одного дня.

## Три состояния и ни одного больше

| Состояние | Что это значит |
|---|---|
| **стоит** | предмет `kind: vehicle` лежит в узле, как станок. В руки он не берётся никогда |
| **впряжён** | тело тянет его по всем переходам. Один за раз, и только тот, кто рядом |
| **гружён** | груз едет **в трюме** на `transport.capacity` килограммов, а не в руках |

**Почему трюм, а не прибавка к рукам.** Прибавкой было бы проще — рюкзак так и
работает через `inventory.carry_bonus`. Но тогда груз распрягшегося возчика
телепортируется ему в руки или испаряется, а сорок мешков зерна обязаны
остаться в повозке там, где она встала. Отдельный контейнер — единственный
способ, при котором «бросить гружёный обоз» является нормальным ходом игры, а
не ошибкой движка (D-157).

## Что решает дорога (D-107)

| Покрытие | Транспорт |
|---|---|
| бездорожье | не проходит вовсе: там идут пешком и на себе |
| дорога | проходит лёгкий, до `transport.heavy_from` |
| мощёный тракт | проходит любой |

Отсюда главное следствие: **дорога — предусловие торговли, а не удобство.** До
узла, найденного разведкой (D-156), телега не доедет, пока к нему не проложат
дорогу; автопуть с обозом строится по проходимым рёбрам и останавливается на
последнем узле, если дальше не пускает покрытие.

## Чего здесь нет и почему

* **Корма и топлива.** `transport.upkeep_per_leg` задан деньгами за отрезок, а
  деньгам в этом мире некуда исчезать (И2): проводка обязана иметь вторую
  сторону, и её ещё нет. Корма как предмета в данных вольта тоже нет;
* **Объёма.** `transport.volume_per_mass` ждёт объёма у предметов — его нет ни
  у чего, и выдумывать данные в коде запрещено (D-065);
* **Экипажа и конвоя.** `transport.crew_ship` ждёт судов, а общий обоз
  нескольких возчиков — своей механики.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.engine import events, world
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.travel import Harness
from src.models.world import Node, Surface
from src.units import AMOUNT_SCALE, amount, amount_float


class TransportError(Exception):
    pass


class NotVehicle(TransportError):
    """Это не транспорт. Впрягаются в повозку, а не в мешок зерна."""


class NotHere(TransportError):
    """Транспорта нет в этом узле. Материя требует присутствия (D-044)."""


class AlreadyHarnessed(TransportError):
    """Уже впряжён. Два обоза одним телом не тянут."""


class NotHarnessed(TransportError):
    """Тело не впряжено: грузить нечего и некуда."""


class Overloaded(TransportError):
    """Трюм полон. Больше грузоподъёмности не увезёт никто."""


class Impassable(TransportError):
    """Покрытие не пускает транспорт (D-107): бездорожье — пешком и на себе."""


def is_vehicle(catalog: Catalog, type_key: str) -> bool:
    """Транспорт ли это. Признак — `kind: vehicle` из вольта, а не имя (D-090)."""
    try:
        return catalog.recipes.recipe(type_key).kind is ItemKind.VEHICLE
    except Exception:  # noqa: BLE001 — сырьё рецептом не описано, и это норма
        return False


def word(constants: Constants, type_key: str) -> str | None:
    """Каким словом вольт называет этот транспорт.

    Ключи `transport.speed_k` — это слова («тачка», «повозка», «корабль»), а
    имена предметов бывают длиннее («Орбитальный корабль»). Списка типов движок
    не держит: заведут в вольте дирижабль — он полетит без правки кода.
    """
    имя = type_key.lower()
    for слово in constants[R.TRANSPORT_SPEED_K]:
        if слово in имя:
            return слово
    return None


def capacity(constants: Constants, type_key: str) -> float:
    """Грузоподъёмность трюма, кг. Вольт не знает такого транспорта — отказ."""
    слово = word(constants, type_key)
    трюмы = constants[R.TRANSPORT_CAPACITY]
    if слово is None or слово not in трюмы:
        raise NotVehicle(
            f"вольт не знает грузоподъёмности «{type_key}»: заведите его в "
            "transport.capacity и transport.speed_k"
        )
    return трюмы[слово]


def speed(constants: Constants, type_key: str) -> float:
    """Во сколько раз быстрее пешего. Тачка медленнее ног, и это её цена."""
    скорости = constants[R.TRANSPORT_SPEED_K]
    return скорости.get(word(constants, type_key), 1.0)


def heavy(constants: Constants, type_key: str) -> bool:
    """Тяжёлый ли транспорт: такому нужен мощёный тракт (D-107)."""
    return capacity(constants, type_key) >= constants[R.TRANSPORT_HEAVY_FROM]


def passable(constants: Constants, surface: Surface, type_key: str) -> bool:
    """Пройдёт ли такой транспорт по такому покрытию (D-107)."""
    if surface is Surface.TRAIL:
        return False
    if surface is Surface.PAVED:
        return True
    return not heavy(constants, type_key)


async def harnessed(session: AsyncSession, body: Body) -> Item | None:
    """Во что тело впряжено сейчас, если впряжено."""
    строка = (
        await session.execute(select(Harness).where(Harness.body_id == body.id))
    ).scalar_one_or_none()
    if строка is None:
        return None
    return await session.get(Item, строка.item_id)


async def harness(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body, item: Item
) -> Item:
    """Впрячься в стоящий здесь транспорт. Присутственно: обоз не телепортируют."""
    from src.engine import travel

    if body.state is not BodyState.ALIVE:
        raise TransportError("мёртвое тело никуда не впрягается")
    await travel.require_here(session, body)

    if not is_vehicle(catalog, item.type_key):
        raise NotVehicle(f"«{item.type_key}» — не транспорт: впрягаются в повозку")
    #: Отказ обязан прийти до упряжки, а не на первом переходе.
    capacity(constants, item.type_key)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover — тело всегда стоит в узле
        raise TransportError("тело вне узла")
    двор = await world.node_container(session, node)
    if item.container_id != двор.id:
        raise NotHere("транспорта нет в этом узле: впрягаются в то, что рядом")

    if await harnessed(session, body) is not None:
        raise AlreadyHarnessed("уже впряжён: сначала распрячься")
    чужая = (
        await session.execute(select(Harness).where(Harness.item_id == item.id))
    ).scalar_one_or_none()
    if чужая is not None:
        raise AlreadyHarnessed("в этот транспорт уже впряжены")

    session.add(Harness(body_id=body.id, item_id=item.id))
    await session.flush()
    await events.record(
        session,
        EventKind.TRANSPORT_HARNESSED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        item_id=str(item.id),
        type_key=item.type_key,
        capacity=capacity(constants, item.type_key),
    )
    return item


async def unharness(session: AsyncSession, body: Body) -> Item | None:
    """Распрячься. Транспорт остаётся стоять здесь вместе с грузом."""
    строка = (
        await session.execute(select(Harness).where(Harness.body_id == body.id))
    ).scalar_one_or_none()
    if строка is None:
        return None
    item = await session.get(Item, строка.item_id)
    await session.delete(строка)
    await session.flush()
    if item is not None:
        await events.record(
            session,
            EventKind.TRANSPORT_UNHARNESSED,
            actor_identity_id=body.identity_id,
            node_id=body.node_id,
            item_id=str(item.id),
            type_key=item.type_key,
        )
    return item


async def drop_missing(session: AsyncSession, item_id: uuid.UUID) -> None:
    """Забыть упряжку, если транспорта больше нет: сломался или уехал."""
    строка = (
        await session.execute(select(Harness).where(Harness.item_id == item_id))
    ).scalar_one_or_none()
    if строка is not None:
        await session.delete(строка)
        await session.flush()


async def cargo(session: AsyncSession, vehicle: Item) -> Container:
    """Трюм этого транспорта. Заводится по первой надобности."""
    stmt = select(Container).where(
        Container.kind == ContainerKind.VEHICLE, Container.owner_id == vehicle.id
    )
    трюм = (await session.execute(stmt)).scalar_one_or_none()
    if трюм is None:
        трюм = Container(kind=ContainerKind.VEHICLE, owner_id=vehicle.id)
        session.add(трюм)
        await session.flush()
    return трюм


async def cargo_items(session: AsyncSession, vehicle: Item) -> list[Item]:
    трюм = await cargo(session, vehicle)
    return list(
        (
            await session.execute(select(Item).where(Item.container_id == трюм.id))
        ).scalars().all()
    )


async def cargo_mass(
    session: AsyncSession, catalog: Catalog, vehicle: Item
) -> float:
    """Сколько килограммов уже везёт трюм."""
    from src.engine import gear

    return sum(
        gear.mass_of(catalog, вещь.type_key, amount_float(вещь.amount))
        for вещь in await cargo_items(session, vehicle)
    )


async def fill(
    session: AsyncSession, constants: Constants, catalog: Catalog, vehicle: Item
) -> float:
    """Насколько полон трюм, доля от 0 до 1. Полный изнашивает вдвое сильнее."""
    предел = capacity(constants, vehicle.type_key)
    if предел <= 0:  # pragma: no cover — нулевой трюм отвергается на упряжке
        return 0.0
    return min(1.0, await cargo_mass(session, catalog, vehicle) / предел)


async def load(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    quantity: float | None = None,
) -> float:
    """Погрузить из рук в трюм. Присутственно: на ходу не перекладывают."""
    from src.engine import gear, travel

    if body.state is not BodyState.ALIVE:
        raise TransportError("мёртвое тело ничего не грузит")
    await travel.require_here(session, body)

    вагон = await harnessed(session, body)
    if вагон is None:
        raise NotHarnessed("грузить некуда: сначала впрячься")
    карман = await world.body_container(session, body)
    if item.container_id != карман.id:
        raise TransportError("этой вещи нет в руках: грузят своё и из рук")

    сколько = amount_float(item.amount) if quantity is None else quantity
    if сколько <= 0:
        raise TransportError("грузить нечего")
    добавка = gear.mass_of(catalog, item.type_key, сколько)
    свободно = capacity(constants, вагон.type_key) - await cargo_mass(
        session, catalog, вагон
    )
    if добавка > свободно:
        raise Overloaded(
            f"в трюме свободно {свободно:.1f} кг, а это {добавка:.1f} кг: "
            "больше грузоподъёмности не увезёт никто"
        )

    трюм = await cargo(session, вагон)
    перенесено = await _move(session, item, трюм, сколько)
    await events.record(
        session,
        EventKind.TRANSPORT_LOADED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(вагон.id),
        type_key=item.type_key,
        amount=перенесено,
        mass=gear.mass_of(catalog, item.type_key, перенесено),
    )
    return перенесено


async def unload(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    quantity: float | None = None,
) -> float:
    """Выгрузить из трюма в руки. Предел рук при этом никуда не девается."""
    from src.engine import gear, travel

    if body.state is not BodyState.ALIVE:
        raise TransportError("мёртвое тело ничего не выгружает")
    await travel.require_here(session, body)

    вагон = await harnessed(session, body)
    if вагон is None:
        raise NotHarnessed("выгружать нечего: сначала впрячься")
    трюм = await cargo(session, вагон)
    if item.container_id != трюм.id:
        raise TransportError("этой вещи нет в трюме")

    сколько = amount_float(item.amount) if quantity is None else quantity
    if сколько <= 0:
        raise TransportError("выгружать нечего")
    await gear.check_carry(session, constants, catalog, body, item.type_key, сколько)

    карман = await world.body_container(session, body)
    перенесено = await _move(session, item, карман, сколько)
    await events.record(
        session,
        EventKind.TRANSPORT_UNLOADED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(вагон.id),
        type_key=item.type_key,
        amount=перенесено,
    )
    return перенесено


async def follow(session: AsyncSession, vehicle: Item, node: Node) -> None:
    """Обоз приехал в узел: сам транспорт и его трюм стоят теперь здесь."""
    двор = await world.node_container(session, node)
    vehicle.container_id = двор.id
    трюм = await cargo(session, vehicle)
    трюм.node_id = node.id
    await session.flush()


async def spill(session: AsyncSession, vehicle: Item, node: Node) -> int:
    """Вывалить груз в узел. Материя не исчезает вместе с тем, что её везло."""
    двор = await world.node_container(session, node)
    вещи = await cargo_items(session, vehicle)
    for вещь in вещи:
        вещь.container_id = двор.id
    await session.flush()
    return len(вещи)


async def wear_leg(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    vehicle: Item,
    node: Node,
) -> bool:
    """Списать износ за отрезок. True — транспорт на этом кончился.

    Полный трюм изнашивает вдвое сильнее пустого: возят не воздух, и
    `wear.transport_per_leg` задан «с поправкой на загрузку» (D-129).
    """
    from src.engine import wear

    загрузка = await fill(session, constants, catalog, vehicle)
    цена = constants[R.WEAR_TRANSPORT_PER_LEG] * (1 + загрузка)

    #: Прибраться надо **до** исчезновения повозки: груз и упряжка ссылаются на
    #: неё, и списывать износ первым значило бы ронять внешний ключ.
    #: Повозка кончается, как всякая вещь (столп П2), но груз остаётся лежать
    #: там, где обоз встал: поломка — остановка, а не потеря груза (D-157).
    if not wear.wears_out(constants, vehicle, цена):
        return await wear.spend(
            session,
            constants,
            vehicle,
            цена,
            cause="переход с обозом",
            actor_identity_id=body.identity_id,
        )

    выпало = await spill(session, vehicle, node)
    await drop_missing(session, vehicle.id)
    await wear.spend(
        session,
        constants,
        vehicle,
        цена,
        cause="переход с обозом",
        actor_identity_id=body.identity_id,
    )
    await events.record(
        session,
        EventKind.TRANSPORT_BROKE,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        type_key=vehicle.type_key,
        spilled=выпало,
    )
    return True


async def view(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body
) -> dict | None:
    """Обоз глазами клиента: во что впряжён, что везёт и куда пройдёт."""
    вагон = await harnessed(session, body)
    if вагон is None:
        return None
    предел = capacity(constants, вагон.type_key)
    груз = [
        {
            "id": str(вещь.id),
            "type_key": вещь.type_key,
            "amount": amount_float(вещь.amount),
            "quality": None if вещь.quality is None else float(вещь.quality),
        }
        for вещь in await cargo_items(session, вагон)
    ]
    return {
        "id": str(вагон.id),
        "type_key": вагон.type_key,
        "condition": float(вагон.condition),
        "capacity": предел,
        "mass": await cargo_mass(session, catalog, вагон),
        "speed_k": speed(constants, вагон.type_key),
        "heavy": heavy(constants, вагон.type_key),
        "cargo": груз,
    }


async def _move(
    session: AsyncSession, item: Item, target: Container, quantity: float
) -> float:
    """Переложить стопку или её часть в другой контейнер.

    Отделённая часть — та же вещь: клеймо, срок, состояние и проба едут вместе
    с ней. Потерять их при делении стопки значило бы обезличить товар.
    """
    сколько = min(amount(quantity), item.amount)
    if сколько >= item.amount:
        item.container_id = target.id
    else:
        item.amount -= сколько
        session.add(
            Item(
                container_id=target.id,
                type_key=item.type_key,
                amount=сколько,
                quality=item.quality,
                condition=item.condition,
                condition_cap=item.condition_cap,
                maker_identity_id=item.maker_identity_id,
                made_at=item.made_at,
                made_node_id=item.made_node_id,
                spoils_at=item.spoils_at,
                flavor=item.flavor,
                roles_filled=item.roles_filled,
                fineness=item.fineness,
            )
        )
    await session.flush()
    return сколько / AMOUNT_SCALE
