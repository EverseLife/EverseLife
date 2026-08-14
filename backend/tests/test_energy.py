"""Энергия: производство, пул города, аккумуляторы, тариф (D-071, D-082, D-085).

Проверяется то, ради чего энергия вынесена в отдельную систему:

* пул один на город, и он есть только у города — вне его работают от батареи;
* станции производят временем и без игроков; угольная без угля мертва;
* энергия не лежит в мешке: только пул или аккумулятор, и тот саморазряжается;
* отпуск идёт по тарифу, деньги — в казну города: бесплатной энергии не бывает.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import energy, ledger, world
from src.models.ledger import AccountKind
from src.models.world import Layer
from src.units import PERCENT, money_str


async def _город(session: AsyncSession, *, река: bool = False):
    """Город: планетный узел-представитель и один узел застройки под ним."""
    метка = uuid.uuid4().hex[:8]
    столица = await world.create_node(
        session, f"terra.city.{метка}", "Столица", area_m2=1, layer=Layer.PLANET
    )
    двор = await world.create_node(
        session, f"terra.city.{метка}.yard", "Двор", area_m2=200,
        layer=Layer.CITY, parent=столица,
        properties={"вода": "река" if река else "нет"},
    )
    identity = await world.create_identity(session, f"Житель-{метка}")
    body = await world.print_body(session, identity, двор)
    return столица, двор, identity, body


async def _поставить(session: AsyncSession, node, что: str, сколько=1, качество=60):
    двор = await world.node_container(session, node)
    return await world.grant_item(
        session, двор, что, amount=сколько, quality=качество, origin="тест"
    )


# --- пул --------------------------------------------------------------------


async def test_пул_есть_у_города_и_нет_вне_его(
    session: AsyncSession, constants: Constants
) -> None:
    """Вне города инфраструктуры нет: там работают от привезённой батареи."""
    _, двор, _, _ = await _город(session)
    assert await energy.pool_of(session, constants, двор) is not None

    дикий = await world.create_node(
        session, f"terra.wild.{uuid.uuid4().hex[:6]}", "Пустошь", area_m2=100,
        layer=Layer.PLANET,
    )
    assert await energy.pool_of(session, constants, дикий) is None


async def test_пул_один_на_город(session: AsyncSession, constants: Constants) -> None:
    """Внутри города энергию никуда не подводят: баланс общий (D-071)."""
    столица, двор, _, _ = await _город(session)
    второй = await world.create_node(
        session, f"{двор.key}.2", "Второй двор", area_m2=100,
        layer=Layer.CITY, parent=столица,
    )
    один = await energy.pool_of(session, constants, двор)
    другой = await energy.pool_of(session, constants, второй)
    assert один is not None and другой is not None
    assert один.id == другой.id


# --- производство -----------------------------------------------------------


async def test_водяное_колесо_работает_только_у_реки(
    session: AsyncSession, constants: Constants
) -> None:
    """География решает: колёса привязывают ранние города к рекам."""
    _, у_реки, _, _ = await _город(session, река=True)
    _, в_степи, _, _ = await _город(session, река=False)
    await _поставить(session, у_реки, energy.WHEEL)
    await _поставить(session, в_степи, energy.WHEEL)

    момент = datetime.now(UTC)
    речной = await energy.pool_of(session, constants, у_реки)
    степной = await energy.pool_of(session, constants, в_степи)
    речной.counted_at = момент - timedelta(hours=1)
    степной.counted_at = момент - timedelta(hours=1)

    дало_реки = await energy.produce(session, constants, речной, now=момент)
    дало_степи = await energy.produce(session, constants, степной, now=момент)

    assert дало_реки == pytest.approx(constants[R.ENERGY_WATERWHEEL_RATE], rel=0.01)
    assert дало_степи == 0


async def test_угольная_станция_жжёт_уголь_и_без_него_мертва(
    session: AsyncSession, constants: Constants
) -> None:
    """Станция без угля мертва — отсюда и энергетическая блокада (D-082)."""
    from src.units import amount_float
    from sqlalchemy import select
    from src.models.inventory import Item

    _, двор, _, _ = await _город(session)
    await _поставить(session, двор, energy.COAL_PLANT)
    await _поставить(session, двор, energy.COAL, сколько=100)

    момент = datetime.now(UTC)
    pool = await energy.pool_of(session, constants, двор)
    pool.counted_at = момент - timedelta(hours=2)
    дало = await energy.produce(session, constants, pool, now=момент)

    сожжено = constants[R.ENERGY_COAL_PLANT_FUEL_DRAW] * 2
    assert дало == pytest.approx(сожжено * constants[R.ENERGY_PER_COAL], rel=0.01)
    #: Ставка вольта сходится с расходом: 4 угля в час дают 200 энергии.
    assert дало == pytest.approx(constants[R.ENERGY_COAL_PLANT_RATE] * 2, rel=0.01)

    контейнер = await world.node_container(session, двор)
    осталось = sum(
        amount_float(и.amount)
        for и in (
            await session.execute(
                select(Item).where(
                    Item.container_id == контейнер.id, Item.type_key == energy.COAL
                )
            )
        ).scalars().all()
    )
    assert осталось == pytest.approx(100 - сожжено)

    #: Уголь кончился — станция встала.
    for стопка in (
        await session.execute(
            select(Item).where(
                Item.container_id == контейнер.id, Item.type_key == energy.COAL
            )
        )
    ).scalars().all():
        await session.delete(стопка)
    await session.flush()
    pool.counted_at = момент
    assert await energy.produce(
        session, constants, pool, now=момент + timedelta(hours=5)
    ) == 0


async def test_ветряк_нестабилен_в_границах_вольта(
    session: AsyncSession, constants: Constants
) -> None:
    """«Зависит от погоды» — всё, что вольт о ветре говорит."""
    _, двор, _, _ = await _город(session)
    await _поставить(session, двор, energy.WINDMILL)
    ветер = constants[R.ENERGY_WINDMILL_RATE]

    момент = datetime.now(UTC)
    pool = await energy.pool_of(session, constants, двор)
    for попытка in range(5):
        pool.counted_at = момент - timedelta(hours=1)
        дало = await energy.produce(
            session, constants, pool, now=момент, rng=random.Random(попытка)
        )
        assert ветер.min <= дало <= ветер.max


# --- аккумулятор и тариф ----------------------------------------------------


async def test_зарядка_берёт_из_пула_и_платит_в_казну(
    session: AsyncSession, constants: Constants
) -> None:
    """Бесплатной энергии не бывает: ноль — это тоже тариф (D-085)."""
    столица, двор, identity, body = await _город(session)
    pool = await energy.pool_of(session, constants, двор)
    pool.stored = Decimal("400")
    pool.counted_at = datetime.now(UTC)

    карман = await world.body_container(session, body)
    батарея = await world.grant_item(
        session, карман, energy.BATTERY, quality=55, origin="тест"
    )
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    from src.models.ledger import PostingReason
    from src.units import money

    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=счёт.id,
        amount=money(100), memo={},
    )

    дали = await energy.charge_battery(session, constants, body, батарея, 200)
    assert дали == pytest.approx(200)
    assert float(pool.stored) == pytest.approx(200)
    assert float(батарея.charge) == pytest.approx(200)

    казна = await ledger.account_for(
        session, AccountKind.CITY_TREASURY, pool.node_id
    )
    #: Тариф задан за сотню энергии: две сотни — два тарифа.
    ожидание = money(2 * constants[R.ENERGY_TARIFF_DEFAULT])
    assert await ledger.balance(session, казна.id) == ожидание
    assert money_str(await ledger.balance(session, счёт.id)) == money_str(
        money(100) - ожидание
    )


async def test_вне_города_заряжать_негде(
    session: AsyncSession, constants: Constants
) -> None:
    метка = uuid.uuid4().hex[:6]
    дикий = await world.create_node(
        session, f"terra.far.{метка}", "Застава", area_m2=100, layer=Layer.PLANET
    )
    identity = await world.create_identity(session, f"Путник-{метка}")
    body = await world.print_body(session, identity, дикий)
    карман = await world.body_container(session, body)
    батарея = await world.grant_item(
        session, карман, energy.BATTERY, quality=55, origin="тест"
    )
    with pytest.raises(energy.NoGrid):
        await energy.charge_battery(session, constants, body, батарея)


async def test_аккумулятор_саморазряжается(
    session: AsyncSession, constants: Constants
) -> None:
    """Энергию нельзя накопить впрок: она скоропортящийся товар.

    Сутки здесь планетарные — `time.day_terra`, те же, что у делянки и у сна:
    у каждой планеты они свои (D-008), и вторых суток в мире нет.
    """
    _, двор, _, body = await _город(session)
    карман = await world.body_container(session, body)
    батарея = await world.grant_item(
        session, карман, energy.BATTERY, quality=55, origin="тест"
    )
    момент = datetime.now(UTC)
    батарея.charge = Decimal("500")
    батарея.charged_at = момент
    await session.flush()

    сутки = timedelta(hours=constants[R.TIME_DAY_TERRA])
    через_сутки = energy.charge_of(constants, батарея, now=момент + сутки)
    утечка = (
        constants[R.ENERGY_BATTERY_CAPACITY]
        * constants[R.ENERGY_BATTERY_SELFDISCHARGE]
        / PERCENT
    )
    assert через_сутки == pytest.approx(500 - утечка)


async def test_энергия_в_мешке_не_лежит(
    session: AsyncSession, constants: Constants
) -> None:
    """Только пул или аккумулятор — третьего вида хранения нет (D-071)."""
    _, двор, _, body = await _город(session)
    карман = await world.body_container(session, body)
    мешок = await world.grant_item(
        session, карман, "Шахтная крепь", quality=50, origin="тест"
    )
    with pytest.raises(energy.NotBattery):
        await energy.charge_battery(session, constants, body, мешок)
