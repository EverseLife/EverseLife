"""Энергия: производство, пул города, аккумуляторы, тариф (D-071, D-082, D-085).

Энергия — не фон, а товар, во что упирается любое развитие. Устроена она
намеренно неудобно для накопления и удобно для политики:

* **производится** станцией, за топливо, которое кто-то добыл и привёз;
* **живёт** либо в пуле города, либо в аккумуляторе — в мешке её нет;
* **отпускается по тарифу**, и бесплатной не бывает: ноль — это тоже тариф.

## Откуда взялась каждая формула

**Выработка.** Ставки заданы вольтом в час и складываются по станциям города:

    водяное колесо  energy.waterwheel_rate      — только там, где река
    ветряк          energy.windmill_rate {0…40} — нестабильно, зависит от погоды
    угольная станция energy.coal_plant_rate     — при energy.coal_plant_fuel_draw
                                                  угля в час

Погоды в мире ещё нет, поэтому ветер разыгрывается броском, засеянным узлом и
часом: «нестабильно» — это и есть всё, что вольт о нём говорит. Угольная
станция **ест уголь из узла**, где стоит: нет подвоза — нет выработки, и
энергетическая блокада работает сама собой, без единой особой механики.

**Саморазряд аккумулятора.** `energy.battery_selfdischarge` процентов в сутки
от ёмкости. Начисляется не тиком, а по факту времени при первом же обращении —
как пар на делянке: незачем будить мир ради заряда, который никто не смотрит.

**Тариф.** `energy.tariff_default` — ТК за сто энергии. Деньги идут в казну
города: энергия отпускается городом, а не природой. Тариф правится уставом с
Э3; до тех пор у пула лежит умолчание вольта.

## Чего здесь пока нет

* **Потребителей, кроме зарядки.** Автоматический станок (D-035), глубокая
  добыча (D-115) и печать тела (D-024) заберут своё вместе со своими
  механиками; пока пул тратится только на аккумуляторы;
* **Счётчика зданий** (D-135): `energy.home_draw_per_m2` считается с площади
  застройки, а построек нет до Э3;
* **Геотермали и реактора**: они за пределами альфы вместе со своими планетами.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import events, ledger, travel, world
from src.models.energy import EnergyPool
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node
from src.units import ENERGY_PER_TARIFF_UNIT, PERCENT, SECONDS_PER_HOUR, money

#: Станции из `build/recipes.json`. Что они дают — ниже, по ставкам вольта.
WHEEL = "Водяное колесо"
WINDMILL = "Ветряк"
COAL_PLANT = "Угольная станция"
BATTERY = "Аккумулятор"
COAL = "Уголь"



class EnergyError(Exception):
    pass


class NoGrid(EnergyError):
    """Вне города пула нет: там работают от аккумулятора, и его надо привезти."""


class NotEnough(EnergyError):
    """В пуле столько нет. Пустой пул — политическое событие, а не ошибка."""


class NotBattery(EnergyError):
    pass


async def grid_node(session: AsyncSession, node: Node) -> Node | None:
    """Узел-представитель города, на территории которого стоит этот узел.

    Городская застройка — дети планетного представителя (D-045). Пойма и балка
    висят прямо на планете и городу не принадлежат: там пула нет.
    """
    if node.parent_id is None:
        return None
    parent = await session.get(Node, node.parent_id)
    if parent is None or parent.layer is not Layer.PLANET:
        return None
    return parent if node.layer is Layer.CITY else None


async def pool_of(
    session: AsyncSession, constants: Constants, node: Node, *, create: bool = True
) -> EnergyPool | None:
    """Пул города этого узла. Заводится при первой надобности."""
    город = await grid_node(session, node)
    if город is None:
        return None
    found = (
        await session.execute(select(EnergyPool).where(EnergyPool.node_id == город.id))
    ).scalar_one_or_none()
    if found is not None or not create:
        return found

    pool = EnergyPool(
        node_id=город.id,
        stored=Decimal(0),
        tariff=Decimal(str(constants[R.ENERGY_TARIFF_DEFAULT])),
    )
    session.add(pool)
    await session.flush()
    return pool


async def produce(
    session: AsyncSession,
    constants: Constants,
    pool: EnergyPool,
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> float:
    """Довести пул до «сейчас»: выработка станций города за прошедшее время.

    Возвращает, сколько добавилось. Топливо списывается там же, где стоит
    станция: город зависит от того, кто возит уголь (D-082).
    """
    moment = now or datetime.now(UTC)
    прошло = (moment - pool.counted_at).total_seconds() / SECONDS_PER_HOUR
    if прошло <= 0:
        return 0.0

    бросок = rng or random.Random(f"{pool.node_id}:{int(moment.timestamp())}")
    узлы = (
        await session.execute(select(Node).where(Node.parent_id == pool.node_id))
    ).scalars().all()

    добавилось = 0.0
    for узел in узлы:
        двор = await world.node_container(session, узел)
        станки = (
            await session.execute(
                select(Item).where(Item.container_id == двор.id)
            )
        ).scalars().all()
        река = узел.properties.get("вода") == "река"

        for станок in станки:
            if станок.type_key == WHEEL and река:
                добавилось += constants[R.ENERGY_WATERWHEEL_RATE] * прошло
            elif станок.type_key == WINDMILL:
                ветер = constants[R.ENERGY_WINDMILL_RATE]
                добавилось += бросок.uniform(ветер.min, ветер.max) * прошло
            elif станок.type_key == COAL_PLANT:
                добавилось += await _burn_coal(
                    session, constants, двор.id, прошло
                )

    pool.stored = Decimal(str(float(pool.stored) + добавилось))
    pool.counted_at = moment
    await session.flush()
    return добавилось


async def _burn_coal(
    session: AsyncSession, constants: Constants, container_id: uuid.UUID, hours: float
) -> float:
    """Сжечь уголь из узла и вернуть выработку. Нет угля — станция стоит."""
    from src.units import amount, amount_float

    надо = constants[R.ENERGY_COAL_PLANT_FUEL_DRAW] * hours
    стопки = (
        await session.execute(
            select(Item).where(Item.container_id == container_id, Item.type_key == COAL)
        )
    ).scalars().all()
    есть = sum(amount_float(стопка.amount) for стопка in стопки)
    сожжём = min(надо, есть)
    if сожжём <= 0:
        return 0.0

    осталось = amount(сожжём)
    for стопка in стопки:
        if осталось <= 0:
            break
        взять = min(осталось, стопка.amount)
        if взять == стопка.amount:
            await session.delete(стопка)
        else:
            стопка.amount -= взять
        осталось -= взять
    await session.flush()
    #: Выработка пропорциональна сожжённому: `energy.per_coal` на единицу.
    return сожжём * constants[R.ENERGY_PER_COAL]


# --- аккумулятор ------------------------------------------------------------


def capacity(constants: Constants) -> float:
    return constants[R.ENERGY_BATTERY_CAPACITY]


def charge_of(
    constants: Constants, item: Item, *, now: datetime | None = None
) -> float:
    """Заряд аккумулятора с учётом саморазряда — по факту прошедшего времени.

    Энергия — скоропортящийся товар: накопить впрок на годы нельзя, и это
    делает её постоянным спросом, а не сокровищем.
    """
    if item.charge is None:
        return 0.0
    moment = now or datetime.now(UTC)
    отсчёт = item.charged_at or item.created_at
    #: Сутки здесь планетарные, как и все прочие сроки мира (D-008).
    часов_в_сутках = constants[R.TIME_DAY_TERRA]
    суток = max(
        0.0, (moment - отсчёт).total_seconds() / SECONDS_PER_HOUR / часов_в_сутках
    )
    утекло = capacity(constants) * constants[R.ENERGY_BATTERY_SELFDISCHARGE] / PERCENT
    return max(0.0, float(item.charge) - утекло * суток)


async def settle_charge(
    session: AsyncSession, constants: Constants, item: Item, *, now: datetime | None = None
) -> float:
    """Записать в аккумулятор его действительный заряд на сейчас."""
    moment = now or datetime.now(UTC)
    заряд = charge_of(constants, item, now=moment)
    item.charge = Decimal(str(заряд))
    item.charged_at = moment
    await session.flush()
    return заряд


async def charge_battery(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    item: Item,
    amount_wanted: float | None = None,
    *,
    now: datetime | None = None,
) -> float:
    """Зарядить аккумулятор из городского пула по тарифу.

    Присутственное: заряд берут в городе и руками. Платит берущий — в казну
    города: бесплатной энергии не бывает, а ноль тоже тариф (D-085).
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise EnergyError("мёртвое тело не заряжает")
    await travel.require_here(session, body)

    if item.type_key != BATTERY:
        raise NotBattery(f"{item.type_key!r} — не аккумулятор: энергия в мешке не лежит")
    карман = await world.body_container(session, body)
    if item.container_id != карман.id:
        raise EnergyError("аккумулятор не в руках")

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise EnergyError("тело вне узла")
    pool = await pool_of(session, constants, node)
    if pool is None:
        raise NoGrid(
            "здесь нет городской сети: вне города работают от аккумулятора, "
            "и заряжают его в городе"
        )
    await produce(session, constants, pool, now=moment)

    есть = await settle_charge(session, constants, item, now=moment)
    место = max(0.0, capacity(constants) - есть)
    хочет = место if amount_wanted is None else min(float(amount_wanted), место)
    дадут = min(хочет, float(pool.stored))
    if дадут <= 0:
        raise NotEnough(
            f"в пуле {float(pool.stored):.0f} энергии, а в аккумуляторе места "
            f"на {место:.0f}"
        )

    #: Тариф задан за сотню энергии — счёт выставляется по нему же.
    цена = money(дадут / ENERGY_PER_TARIFF_UNIT * float(pool.tariff))
    if цена > 0:
        счёт = await ledger.account_for(session, AccountKind.IDENTITY, body.identity_id)
        казна = await ledger.account_for(
            session, AccountKind.CITY_TREASURY, pool.node_id
        )
        await ledger.transfer(
            session,
            PostingReason.ENERGY_BILL,
            debit=счёт.id,
            credit=казна.id,
            amount=цена,
            memo={"энергии": дадут, "тариф": float(pool.tariff)},
        )

    pool.stored = Decimal(str(float(pool.stored) - дадут))
    item.charge = Decimal(str(есть + дадут))
    item.charged_at = moment
    await session.flush()

    await events.record(
        session,
        EventKind.ENERGY_CHARGED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(item.id),
        energy=дадут,
        paid=цена,
        tariff=float(pool.tariff),
    )
    return дадут


async def draw_for_work(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    energy_needed: float,
    *,
    what: str,
    now: datetime | None = None,
) -> int:
    """Отпустить энергию из пула на работу и выставить счёт по тарифу.

    Возвращает уплаченное. Платит тот, кто жжёт (D-135): владелец станка
    участвует в расходах на топливо наравне со всеми, иначе энергетика
    перестаёт быть экономикой и становится дотацией.

    Списывается **вперёд**, как и материалы партии: тогда не возникает вопроса,
    что делать с начатой работой при опустевшем пуле, — он в вольте открыт
    (12-energy), и решать его молча движок не вправе.
    """
    moment = now or datetime.now(UTC)
    if energy_needed <= 0:
        return 0

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise EnergyError("тело вне узла")
    pool = await pool_of(session, constants, node)
    if pool is None:
        raise NoGrid(
            f"{what} требует энергии, а городской сети здесь нет: вне города "
            "работают от аккумулятора"
        )
    await produce(session, constants, pool, now=moment)

    if float(pool.stored) < energy_needed:
        raise NotEnough(
            f"{what} требует {energy_needed:.0f} энергии, а в пуле "
            f"{float(pool.stored):.0f}: город без топлива стоит"
        )

    цена = money(energy_needed / ENERGY_PER_TARIFF_UNIT * float(pool.tariff))
    if цена > 0:
        счёт = await ledger.account_for(session, AccountKind.IDENTITY, body.identity_id)
        казна = await ledger.account_for(
            session, AccountKind.CITY_TREASURY, pool.node_id
        )
        await ledger.transfer(
            session,
            PostingReason.ENERGY_BILL,
            debit=счёт.id,
            credit=казна.id,
            amount=цена,
            memo={"энергии": energy_needed, "за": what, "тариф": float(pool.tariff)},
        )

    pool.stored = Decimal(str(float(pool.stored) - energy_needed))
    await session.flush()
    await events.record(
        session,
        EventKind.ENERGY_DRAWN,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        energy=energy_needed,
        paid=цена,
        work=what,
    )
    return цена


async def price_of(
    session: AsyncSession, constants: Constants, node: Node, energy_needed: float
) -> int:
    """Во что обойдётся столько энергии здесь. Для прогноза — до траты."""
    pool = await pool_of(session, constants, node, create=False)
    тариф = float(pool.tariff) if pool is not None else constants[R.ENERGY_TARIFF_DEFAULT]
    return money(energy_needed / ENERGY_PER_TARIFF_UNIT * тариф)


async def tick_pools(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> float:
    """Довести все городские пулы до «сейчас». Мир живёт без игроков."""
    moment = now or datetime.now(UTC)
    pools = (await session.execute(select(EnergyPool))).scalars().all()
    итог = 0.0
    for pool in pools:
        итог += await produce(session, constants, pool, now=moment)
    return итог


async def ensure_pools(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> int:
    """Завести пул каждому городу, у которого есть застройка.

    Город — узел планетного слоя, под которым стоят узлы городского. Пул
    заводится один раз и дальше живёт временем.
    """
    города = (
        await session.execute(select(Node).where(Node.layer == Layer.CITY))
    ).scalars().all()
    заведено = 0
    for узел in города:
        if await pool_of(session, constants, узел, create=False) is None:
            if await pool_of(session, constants, узел) is not None:
                заведено += 1
    return заведено
