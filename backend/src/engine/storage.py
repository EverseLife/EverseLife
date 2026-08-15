"""Хранилище: сундук, стеллаж и всё, у чего в вольте есть `store` (D-181).

Носимое ограничено (D-146), обоз возит (D-157) — а **положить** до сих пор было
некуда: нажитое ездило в руках либо лежало в терминале, то есть на продаже.
Дом как место, где хранят, начинается здесь.

## Что делает вещь хранилищем

Число в вольте, а не имя в коде: `store` — вместимость в килограммах. Заведут
в данных новый ларь — он заработает без правки движка (D-090). Мебель это или
станок, движку безразлично: он смотрит на поле.

## Правила

* **присутственно** — в чужой сундук через полкарты не лезут;
* **распоряжается тот, кто вправе распоряжаться узлом** (`station.may_build`):
  хозяин, а на городской земле — власть. Вскрыть чужой сундук — дело суда
  (D-166), а не кнопки;
* **предел — масса**, теми же килограммами, что руки и трюм: третьей единицы
  вместимости в мире нет;
* **полное хранилище не уносят** (`station.take`): иначе «забрать мебель»
  стало бы способом унести тонну груза в кармане.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import events, travel, world
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Node
from src.units import amount_float


class StorageError(Exception):
    pass


class NotStorage(StorageError):
    """Это не хранилище: у вещи нет вместимости в вольте."""


class NotYours(StorageError):
    """Чужой сундук не открывают: доступ идёт за правом на узел (D-181)."""


class Full(StorageError):
    """Больше не влезет. Предел — масса, как у рук и у трюма."""


def capacity(catalog: Catalog, type_key: str) -> float | None:
    """Вместимость вещи как хранилища, кг. `None` — вещь не хранилище."""
    try:
        return catalog.recipes.recipe(type_key).store
    except Exception:  # noqa: BLE001 — сырьё рецептом не описано, и это норма
        return None


def is_storage(catalog: Catalog, type_key: str) -> bool:
    предел = capacity(catalog, type_key)
    return предел is not None and предел > 0


async def inside(session: AsyncSession, chest: Item) -> Container:
    """Нутро хранилища. Заводится по первой надобности."""
    stmt = select(Container).where(
        Container.kind == ContainerKind.STORAGE, Container.owner_id == chest.id
    )
    контейнер = (await session.execute(stmt)).scalar_one_or_none()
    if контейнер is None:
        контейнер = Container(kind=ContainerKind.STORAGE, owner_id=chest.id)
        session.add(контейнер)
        await session.flush()
    return контейнер


async def content(session: AsyncSession, chest: Item) -> list[Item]:
    контейнер = await inside(session, chest)
    return list(
        (
            await session.execute(select(Item).where(Item.container_id == контейнер.id))
        ).scalars().all()
    )


async def stored_mass(
    session: AsyncSession, catalog: Catalog, chest: Item
) -> float:
    """Сколько килограммов уже лежит внутри."""
    from src.engine import gear

    return sum(
        gear.mass_of(catalog, вещь.type_key, amount_float(вещь.amount))
        for вещь in await content(session, chest)
    )


async def is_empty(session: AsyncSession, chest: Item) -> bool:
    """Пусто ли внутри. По этому решается, отдавать ли мебель в руки."""
    контейнер = await inside(session, chest)
    found = await session.scalar(
        select(Item.id).where(Item.container_id == контейнер.id).limit(1)
    )
    return found is None


async def put(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    chest: Item,
    item: Item,
    quantity: float | None = None,
) -> float:
    """Положить из рук в хранилище. Присутственно и только в своё."""
    await _allowed(session, catalog, body, chest)

    карман = await world.body_container(session, body)
    if item.container_id != карман.id:
        raise StorageError("этой вещи нет в руках: кладут своё и из рук")

    сколько = amount_float(item.amount) if quantity is None else quantity
    if сколько <= 0:
        raise StorageError("класть нечего")

    from src.engine import gear

    добавка = gear.mass_of(catalog, item.type_key, сколько)
    предел = capacity(catalog, chest.type_key) or 0.0
    свободно = предел - await stored_mass(session, catalog, chest)
    if добавка > свободно:
        raise Full(
            f"в «{chest.type_key}» свободно {свободно:.1f} кг, а это "
            f"{добавка:.1f} кг"
        )

    нутро = await inside(session, chest)
    перенесено = await world.move_stack(session, item, нутро, сколько)
    await events.record(
        session,
        EventKind.STORAGE_PUT,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(chest.id),
        type_key=item.type_key,
        amount=перенесено,
    )
    return перенесено


async def take(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    chest: Item,
    item: Item,
    quantity: float | None = None,
) -> float:
    """Забрать из хранилища в руки. Предел рук при этом никуда не девается."""
    await _allowed(session, catalog, body, chest)

    нутро = await inside(session, chest)
    if item.container_id != нутро.id:
        raise StorageError("этой вещи нет в хранилище")

    сколько = amount_float(item.amount) if quantity is None else quantity
    if сколько <= 0:
        raise StorageError("забирать нечего")

    from src.engine import gear

    await gear.check_carry(session, constants, catalog, body, item.type_key, сколько)

    карман = await world.body_container(session, body)
    перенесено = await world.move_stack(session, item, карман, сколько)
    await events.record(
        session,
        EventKind.STORAGE_TAKEN,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(chest.id),
        type_key=item.type_key,
        amount=перенесено,
    )
    return перенесено


async def _allowed(
    session: AsyncSession, catalog: Catalog, body: Body, chest: Item
) -> Node:
    """Общая дверь обоих действий: живой, здесь, вправе и это хранилище."""
    from src.engine import station

    if body.state is not BodyState.ALIVE:
        raise StorageError("мёртвое тело ничего не перекладывает")
    await travel.require_here(session, body)
    if not is_storage(catalog, chest.type_key):
        raise NotStorage(f"«{chest.type_key}» — не хранилище: в него не кладут")

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover — тело без узла это баг
        raise StorageError("тело вне узла")
    двор = await world.node_container(session, node)
    if chest.container_id != двор.id:
        raise StorageError("этого хранилища здесь нет")
    if not await station.may_build(session, body, node):
        raise NotYours(
            "хранилище не ваше: в чужой сундук не лезут. Открыть его вправе "
            "хозяин узла, а на городской земле — власть"
        )
    return node
