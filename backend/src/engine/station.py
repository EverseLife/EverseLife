"""Станок и мебель ставят в здание и уносят из него (D-106, D-150).

Станок ставится **в здание**: на пустом участке сначала строят
(`estate.construct`), и только потом обставляют. Станки и мебель занимают
площадь — `build.slots_per_area` квадратных метров на вещь, — поэтому площадь
дома это его вместимость, а не декорация.

Правило владения простое и на нём всё держится:

* **свой узел** — ставит и уносит хозяин;
* **городской узел** — ставит и уносит тот, кому город дал полномочие `laws`:
  чем застроен город, решает власть, а не случайный прохожий;
* **ничей узел** — никак: сначала участок занимают либо выкупают.

Станок — предмет `kind: station`, мебель — `kind: furniture` из
`build/recipes.json`. Списка «что является станком» движок не держит: заведут
в вольте новый — он поставится без правки кода (D-090). Разница между ними
одна: за станком работают, мебель обустраивает быт (кровать — гибернация,
стеллаж — хранение), и клиент показывает их отдельными окнами.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog
from src.constants.catalog import ItemKind
from src.engine import events, travel, world
from src.models.city import Power
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.world import Node


class StationError(Exception):
    pass


class NotStation(StationError):
    """Это не станок и не мебель. В здание ставят оборудование, а не мешок зерна."""


class NotYours(StationError):
    """Узел не ваш. Станок ставят у себя — в этом и смысл своего дома."""


class Busy(StationError):
    """Станок занят работой: унести его из-под работающего нельзя."""


def is_station(catalog: Catalog, type_key: str) -> bool:
    try:
        return catalog.recipes.recipe(type_key).kind is ItemKind.STATION
    except Exception:  # noqa: BLE001 — сырьё рецептом не описано, и это норма
        return False


def is_furniture(catalog: Catalog, type_key: str) -> bool:
    try:
        return catalog.recipes.recipe(type_key).kind is ItemKind.FURNITURE
    except Exception:  # noqa: BLE001
        return False


def placeable(catalog: Catalog, type_key: str) -> bool:
    """Что вообще ставится в здание: станок либо мебель."""
    return is_station(catalog, type_key) or is_furniture(catalog, type_key)


async def may_build(session: AsyncSession, body: Body, node: Node) -> bool:
    """Вправе ли это тело ставить и уносить оборудование в этом узле."""
    from src.engine import city as town

    if node.owner_identity_id == body.identity_id:
        return True
    if node.owner_city_id is None:
        return False
    город = await town.by_id(session, node.owner_city_id)
    return город is not None and await town.may(
        session, body.identity_id, город, Power.LAWS
    )


async def place(
    session: AsyncSession, catalog: Catalog, body: Body, item: Item
) -> Item:
    """Поставить станок либо мебель из рук в здание узла.

    Присутственное: станки не телепортируют. Требует здания со свободным
    местом: станок занимает площадь, и в двор под открытым небом он не встаёт
    (D-106).
    """
    from src.constants import current
    from src.engine import estate

    if body.state is not BodyState.ALIVE:
        raise StationError("мёртвое тело ничего не ставит")
    await travel.require_here(session, body)

    карман = await world.body_container(session, body)
    if item.container_id != карман.id:
        raise StationError("этой вещи нет в руках")
    if not placeable(catalog, item.type_key):
        raise NotStation(
            f"«{item.type_key}» — не станок и не мебель: в здание ставят оборудование"
        )

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise StationError("тело вне узла")
    if not await may_build(session, body, node):
        raise NotYours(
            "узел не ваш: оборудование ставят у себя. Пустой городской участок "
            "выкупают, дикий — занимают"
        )

    #: Здание — вместимость: `build.slots_per_area` м² на вещь. Нет здания —
    #: нет и места; двор остаётся двором.
    constants = current()
    всего, занято = await estate.slots(session, constants, node)
    if всего <= 0:
        raise estate.NoBuilding(
            "на участке нет здания: сначала строят, потом обставляют (D-106)"
        )
    if занято >= всего:
        from src.constants import registry as R

        raise estate.NoRoom(
            f"в здании {всего} мест по {constants[R.BUILD_SLOTS_PER_AREA]:g} м², "
            "и все заняты: стройте больше либо уносите лишнее"
        )

    двор = await world.node_container(session, node)
    item.container_id = двор.id
    await session.flush()

    await events.record(
        session,
        EventKind.STATION_PLACED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        item_id=str(item.id),
        type_key=item.type_key,
    )
    return item


async def take(
    session: AsyncSession, catalog: Catalog, body: Body, item: Item
) -> Item:
    """Забрать станок либо мебель обратно в руки. Занятый работой не отдаётся."""
    if body.state is not BodyState.ALIVE:
        raise StationError("мёртвое тело ничего не уносит")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise StationError("тело вне узла")
    двор = await world.node_container(session, node)
    if item.container_id != двор.id:
        raise StationError("этой вещи нет в этом узле")
    if not placeable(catalog, item.type_key):
        raise NotStation(f"«{item.type_key}» — не станок и не мебель")
    if not await may_build(session, body, node):
        raise NotYours("узел не ваш: чужое оборудование не уносят")
    if item.busy_body_id is not None:
        raise Busy("за станком работают: дождитесь конца партии")

    карман = await world.body_container(session, body)
    item.container_id = карман.id
    await session.flush()

    await events.record(
        session,
        EventKind.STATION_TAKEN,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        item_id=str(item.id),
        type_key=item.type_key,
    )
    return item
