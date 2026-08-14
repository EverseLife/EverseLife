"""Счётчик быта: кто платит и что бывает, если не заплатил (D-135, D-149).

Проверяется ровно то, ради чего счётчик заведён:

* платит **владелец**, а за городское — казна, и то не деньгами, а энергией,
  которую могла бы продать;
* ничей узел счёта не порождает вовсе: платить некому, а деньгам исчезать
  некуда (И2);
* не заплатил — узел отключён, и станки в нём не работают до оплаты;
* вне города счётчика нет: там нет сети.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import craft, energy, ledger, utility, world
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer
from src.units import money


async def _город(session: AsyncSession, catalog: Catalog):
    метка = uuid.uuid4().hex[:8]
    планета = await world.create_node(
        session, f"terra.{метка}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    представитель = await world.create_node(
        session, f"terra.city.{метка}", "Столица", area_m2=1,
        layer=Layer.PLANET, parent=планета,
    )
    дом = await world.create_node(
        session, f"terra.city.{метка}.home", "Дом", area_m2=100, parent=представитель
    )
    город = await town.found(session, catalog, представитель, "Столица")
    дом.owner_city_id = город.id
    await session.flush()
    return город, представитель, дом


async def _пул(session: AsyncSession, constants: Constants, узел, сколько: float):
    pool = await energy.pool_of(session, constants, узел)
    assert pool is not None
    pool.stored = Decimal(str(сколько))
    await session.flush()
    return pool


async def _житель(session: AsyncSession, узел, имя: str, *, денег: float = 0):
    identity = await world.create_identity(session, f"{имя}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, узел)
    if денег:
        счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session, PostingReason.GENESIS,
            debit=genesis.id, credit=счёт.id, amount=money(денег),
        )
    return identity, body


def _вчера(constants: Constants) -> datetime:
    return datetime.now(UTC) - timedelta(hours=constants[R.ENERGY_METER_PERIOD])


async def test_у_ничьего_узла_счётчика_нет(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Выставлять счёт некому — значит и счётчика нет."""
    _, _, дом = await _город(session, catalog)
    дом.owner_city_id = None
    await session.flush()
    assert await utility.meter_of(session, дом) is None


async def test_вне_города_счётчика_нет(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Там нет сети: работают от аккумулятора, и коммунальных отношений нет."""
    identity = await world.create_identity(session, f"Ферма-{uuid.uuid4().hex[:6]}")
    пойма = await world.create_node(
        session, f"terra.wild.{uuid.uuid4().hex[:8]}", "Пойма", area_m2=400,
        layer=Layer.PLANET,
    )
    пойма.owner_identity_id = identity.id
    await session.flush()
    assert await utility.meter_of(session, пойма) is None


async def test_владелец_платит_за_быт(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Счёт считается с площади и списывается по тарифу города (D-135)."""
    город, представитель, дом = await _город(session, catalog)
    хозяин, _ = await _житель(session, дом, "Хозяин", денег=100)
    дом.owner_identity_id = хозяин.id
    await session.flush()

    pool = await _пул(session, constants, дом, 100_000)
    meter = await utility.meter_of(session, дом)
    meter.counted_at = _вчера(constants)
    await session.flush()

    начислено = await utility.bill(session, constants, дом)
    assert начислено > 0

    счёт = await ledger.account_for(session, AccountKind.IDENTITY, хозяин.id)
    assert await ledger.balance(session, счёт.id) == money(100) - начислено
    #: Деньги ушли в казну города, энергия — из пула: счётчик не выдумывает
    #: расход, а списывает его.
    assert await town.treasury_balance(session, город) == начислено
    assert float(pool.stored) < 100_000
    assert not meter.cut_off


async def test_за_городское_платит_казна_энергией(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Город не платит сам себе деньгами, но платит энергией (D-149)."""
    город, представитель, дом = await _город(session, catalog)
    pool = await _пул(session, constants, дом, 100_000)
    было = float(pool.stored)

    meter = await utility.meter_of(session, дом)
    meter.counted_at = _вчера(constants)
    await session.flush()

    assert await utility.bill(session, constants, дом) == 0, "казна не платит себе"
    assert float(pool.stored) < было, "энергия всё равно ушла"
    assert await town.treasury_balance(session, город) == 0


async def test_нечем_платить_узел_отключается(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Долг остаётся на узле, узел отключён. Отобрать его движок не вправе."""
    город, представитель, дом = await _город(session, catalog)
    хозяин, тело = await _житель(session, дом, "Бедняк")
    дом.owner_identity_id = хозяин.id
    await session.flush()

    await _пул(session, constants, дом, 100_000)
    meter = await utility.meter_of(session, дом)
    meter.counted_at = _вчера(constants)
    await session.flush()

    начислено = await utility.bill(session, constants, дом)
    assert начислено > 0
    assert meter.cut_off and meter.debt == начислено
    assert await utility.cut_off(session, дом)

    #: Отключённый узел не работает станками: счётчик — такое же условие
    #: работы, как сам станок (D-149).
    двор = await world.node_container(session, дом)
    await world.grant_item(session, двор, "Верстак", quality=60, origin="сценарий теста")
    await world.learn(session, хозяин, "Брус")
    with pytest.raises(craft.CutOff):
        await craft.plan(session, constants, catalog, тело, "Брус", 1)


async def test_оплата_включает_узел_обратно(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, представитель, дом = await _город(session, catalog)
    хозяин, _ = await _житель(session, дом, "Должник")
    дом.owner_identity_id = хозяин.id
    await session.flush()

    await _пул(session, constants, дом, 100_000)
    meter = await utility.meter_of(session, дом)
    meter.counted_at = _вчера(constants)
    await session.flush()
    долг = await utility.bill(session, constants, дом)
    assert meter.cut_off

    #: Денег всё ещё нет — платить нечем, и это отказ, а не молчание.
    with pytest.raises(utility.NotEnoughMoney):
        await utility.pay(session, constants, хозяин, дом)

    счёт = await ledger.account_for(session, AccountKind.IDENTITY, хозяин.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.GENESIS,
        debit=genesis.id, credit=счёт.id, amount=долг,
    )
    assert await utility.pay(session, constants, хозяин, дом) == долг
    assert not meter.cut_off and meter.debt == 0
    assert await town.treasury_balance(session, город) == долг


async def test_чужой_счёт_не_оплатишь(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Чужие счета оплачивает договор, а не движок."""
    _, _, дом = await _город(session, catalog)
    хозяин, _ = await _житель(session, дом, "Хозяин")
    чужой, _ = await _житель(session, дом, "Чужой", денег=100)
    дом.owner_identity_id = хозяин.id
    await session.flush()
    await _пул(session, constants, дом, 100_000)
    meter = await utility.meter_of(session, дом)
    meter.counted_at = _вчера(constants)
    await session.flush()
    await utility.bill(session, constants, дом)

    with pytest.raises(utility.UtilityError):
        await utility.pay(session, constants, чужой, дом)


async def test_счётчик_заводится_сам_на_занятые_узлы(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Иначе первого счёта неоткуда взяться: счётчик ждал бы сам себя."""
    _, _, дом = await _город(session, catalog)
    assert await utility.meter_of(session, дом, create=False) is None
    выставлено = await utility.run_meters(session, constants)
    assert выставлено >= 1
    assert await utility.meter_of(session, дом, create=False) is not None


async def test_хозяйство_показывает_свои_узлы(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Пустой список — не поломка панели, а «владений нет»."""
    _, _, дом = await _город(session, catalog)
    хозяин, _ = await _житель(session, дом, "Хозяин")
    assert await utility.holdings(session, constants, хозяин.id) == []

    дом.owner_identity_id = хозяин.id
    await session.flush()
    свои = await utility.holdings(session, constants, хозяин.id)
    assert len(свои) == 1
    assert свои[0]["node"] == дом.key
    assert свои[0]["grid"] is True
    assert свои[0]["cost_per_period"] > 0
