"""Носимое: масса, предел и слоты снаряжения (D-146, D-129).

Предел носимого записан в вольте с самого начала — `inventory.carry_mass`, «всё
сверх — только транспортом», — но у предметов не было массы, и он ничего не
значил. Игрок носил в кармане тысячу руды, и география, ради которой всё
строилось, ничего не стоила.

## Как считается

**Нагрузка** — сумма масс всего, что в руках, включая надетое: экзоскелет не
становится невесомым оттого, что его надели.

**Предел** — `inventory.carry_mass` плюс `inventory.carry_bonus` за каждую
надетую вещь. Рюкзак и экзоскелет поднимают его, одежда и броня слот занимают,
но переносимого не добавляют — их эффект приедет со средой и боем.

**Слот один на вещь.** Без слотов игрок надел бы три рюкзака, и предел перестал
бы существовать; слот — это и есть ограничение, а не украшение интерфейса.

## Где предел проверяется

Там, где игрок **берёт вещь в руки**: покупка из терминала, уборка урожая,
вывоз бункера. Это не сообщение об ошибке, а причина существования повозок,
караванов и профессии возчика.

Изготовленное у станка под предел не попадает: оно ложится там, где сделано, и
станет ношей только когда его возьмут. Так же и с добытым в забое — оно
остаётся в забое, пока за ним не пришли.

## Чего здесь пока нет

* **Объёма.** `inventory.carry_volume` в вольте есть, объёма у предметов —
  нет. Заводить его в коде значило бы придумать данные, которых нет (D-065);
* **Транспорта.** Он и есть ответ на предел (D-107), и приедет со своей
  механикой: у груза наконец появилась масса, а `transport.mass_*` ждали её.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, travel, world
from src.models.event import EventKind
from src.models.gear import Equipped
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.units import amount_float


class GearError(Exception):
    pass


class NotGear(GearError):
    """Эту вещь не надевают: слот у предмета берётся из данных вольта."""


class Overloaded(GearError):
    """Больше предела в руки не берут. Всё сверх — только транспортом."""


def mass_of(catalog: Catalog, type_key: str, quantity: float) -> float:
    """Масса такого количества этого предмета, кг."""
    return catalog.recipes.mass_of(type_key) * quantity


async def load_of(
    session: AsyncSession, catalog: Catalog, body: Body
) -> float:
    """Сколько тело несёт сейчас, кг. Надетое считается вместе со всем."""
    карман = await world.body_container(session, body)
    вещи = (
        await session.execute(select(Item).where(Item.container_id == карман.id))
    ).scalars().all()
    return sum(
        mass_of(catalog, вещь.type_key, amount_float(вещь.amount)) for вещь in вещи
    )


async def equipped(session: AsyncSession, body: Body) -> dict[str, Item]:
    """Что надето: слот → вещь."""
    строки = (
        await session.execute(select(Equipped).where(Equipped.body_id == body.id))
    ).scalars().all()
    итог: dict[str, Item] = {}
    for строка in строки:
        вещь = await session.get(Item, строка.item_id)
        if вещь is not None:
            итог[строка.slot] = вещь
    return итог


async def capacity(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body
) -> float:
    """Предел носимого с учётом надетого, кг."""
    бонусы = constants[R.INVENTORY_CARRY_BONUS]
    надето = await equipped(session, body)
    прибавка = sum(
        бонусы.get(catalog.recipes.resolve(вещь.type_key), 0.0)
        for вещь in надето.values()
    )
    return constants[R.INVENTORY_CARRY_MASS] + прибавка


async def check_carry(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    type_key: str,
    quantity: float,
) -> None:
    """Влезет ли это в руки. Не влезло — не берут, и это не ошибка, а вес."""
    добавка = mass_of(catalog, type_key, quantity)
    if добавка <= 0:
        return
    несёт = await load_of(session, catalog, body)
    предел = await capacity(session, constants, catalog, body)
    if несёт + добавка > предел:
        raise Overloaded(
            f"не унести: в руках {несёт:.1f} кг из {предел:.0f}, "
            f"а это ещё {добавка:.1f} кг. Всё сверх — только транспортом"
        )


async def equip(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    *,
    now: datetime | None = None,
) -> str:
    """Надеть вещь. Слот занят — прежняя снимается сама.

    Присутственное только в том смысле, что вещь должна быть в руках: надеть
    рюкзак, лежащий в другом городе, нельзя.
    """
    if body.state is not BodyState.ALIVE:
        raise GearError("мёртвое тело не одевается")
    await travel.require_here(session, body)

    slot = catalog.recipes.slot_of(item.type_key)
    if slot is None:
        raise NotGear(f"{item.type_key!r} не надевается: у него нет слота")
    if slot not in catalog.recipes.gear_slots:  # pragma: no cover — данные вольта
        raise NotGear(f"слота {slot!r} в мире нет")

    карман = await world.body_container(session, body)
    if item.container_id != карман.id:
        raise GearError("вещь не в руках: надевают своё")

    прежнее = (
        await session.execute(
            select(Equipped).where(Equipped.body_id == body.id, Equipped.slot == slot)
        )
    ).scalar_one_or_none()
    if прежнее is not None:
        if прежнее.item_id == item.id:
            return slot
        await session.delete(прежнее)
        await session.flush()

    session.add(Equipped(body_id=body.id, slot=slot, item_id=item.id))
    await session.flush()
    await events.record(
        session,
        EventKind.GEAR_EQUIPPED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(item.id),
        type_key=item.type_key,
        slot=slot,
    )
    return slot


async def unequip(
    session: AsyncSession, body: Body, slot: str
) -> Item | None:
    """Снять надетое из слота. Вещь остаётся в руках — она и так была там."""
    строка = (
        await session.execute(
            select(Equipped).where(Equipped.body_id == body.id, Equipped.slot == slot)
        )
    ).scalar_one_or_none()
    if строка is None:
        return None
    вещь = await session.get(Item, строка.item_id)
    await session.delete(строка)
    await session.flush()
    await events.record(
        session,
        EventKind.GEAR_UNEQUIPPED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(строка.item_id),
        slot=slot,
    )
    return вещь


async def drop_missing(session: AsyncSession, item_id: uuid.UUID) -> None:
    """Снять запись о надетом, если вещи больше нет.

    Вещь может кончиться износом или уехать на рынок — слот не должен помнить
    то, чего нет.
    """
    строка = (
        await session.execute(select(Equipped).where(Equipped.item_id == item_id))
    ).scalar_one_or_none()
    if строка is not None:
        await session.delete(строка)
        await session.flush()
