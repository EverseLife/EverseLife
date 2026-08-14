"""Автоматический станок: промышленность против ремесла (D-035, D-058, D-129).

Разделение укладов держится не на навыке, а на внимании и на счёте за энергию:

* автомат вдвое быстрее и не требует инструмента — потолок задаёт он сам;
* результат ровный: премии ремесла нет, пропорция — его настройка;
* мастер всё равно может превзойти станок, подстроившись под сырьё;
* автомат ест энергию из городского пула и платит по тарифу, ручной верстак
  не потребляет ничего — ремесло доступно и без денег на счета.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import craft, energy, ledger, world
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer
from src.units import MINUTES_PER_HOUR, money

INGOT = "Слиток железа"
NAILS = "Гвозди"
#: Гвозди куются: ручной уклад теста живёт в кузнице (ребаланс станков).
BENCH = "Кузница"


async def _цех(session: AsyncSession, *, автомат: bool = True, качество: float = 60):
    """Городской узел со станком: город нужен ради пула энергии."""
    метка = uuid.uuid4().hex[:8]
    столица = await world.create_node(
        session, f"terra.town.{метка}", "Город", area_m2=1, layer=Layer.PLANET
    )
    цех = await world.create_node(
        session, f"terra.town.{метка}.shop", "Цех", area_m2=200,
        layer=Layer.CITY, parent=столица,
    )
    двор = await world.node_container(session, цех)
    await world.grant_item(
        session, двор, craft.AUTO_BENCH if автомат else BENCH,
        quality=качество, origin="тест",
    )
    identity = await world.create_identity(session, f"Промышленник-{метка}")
    body = await world.print_body(session, identity, цех)
    await world.learn(session, identity, NAILS)

    карман = await world.body_container(session, body)
    await world.grant_item(
        session, карман, INGOT, amount=50, quality=70, origin="тест"
    )
    return цех, identity, body


async def _пул(session, constants, цех, *, сколько: float = 5000):
    pool = await energy.pool_of(session, constants, цех)
    pool.stored = Decimal(str(сколько))
    pool.counted_at = datetime.now(UTC)
    await session.flush()
    return pool


async def _денег(session, identity, сколько: float = 500):
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=счёт.id,
        amount=money(сколько), memo={},
    )
    return счёт


# --- уклады -----------------------------------------------------------------


async def test_автомат_вдвое_быстрее(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`craft.auto_speed_k` — во столько раз он быстрее. Объём против качества."""
    цех, identity, body = await _цех(session)
    await _пул(session, constants, цех)
    await _денег(session, identity)
    #: Тот же станок, но ручным укладом: сравниваем именно уклады.
    двор = await world.node_container(session, цех)
    await world.grant_item(session, двор, BENCH, quality=60, origin="тест")

    руками = await craft.plan(session, constants, catalog, body, NAILS, 2)
    автоматом = await craft.plan(session, constants, catalog, body, NAILS, 2, auto=True)

    assert автоматом.minutes == pytest.approx(
        руками.minutes / constants[R.CRAFT_AUTO_SPEED_K]
    )
    assert автоматом.auto and not руками.auto


async def test_автомату_инструмент_не_нужен_а_ремеслу_нужен(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Потолок задаёт станок: у промышленности инструмента нет вовсе."""
    цех, identity, body = await _цех(session)
    await _пул(session, constants, цех)
    await _денег(session, identity)

    план = await craft.plan(session, constants, catalog, body, NAILS, 1, auto=True)
    assert план.ceiling == pytest.approx(60), "потолок — качество автомата"


async def test_ремесло_может_превзойти_станок(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Человек подстраивается под сырьё, станок работает по настройке всегда.

    Сравнивать надо на **смеси**: премия ремесла есть только там, где есть
    пропорции. У сборки их нет вовсе, и там укладам спорить не о чем (D-092).
    """
    стекло = "Стекло"
    цех, identity, body = await _цех(session, автомат=True, качество=80)
    двор = await world.node_container(session, цех)
    await world.grant_item(session, двор, "Плавильная печь", quality=80, origin="тест")
    await world.learn(session, identity, стекло)
    await _пул(session, constants, цех)
    await _денег(session, identity)

    карман = await world.body_container(session, body)
    for сырьё in ("Кварцевый песок", "Уголь"):
        await world.grant_item(
            session, карман, сырьё, amount=100, quality=60, origin="тест"
        )

    руками = await craft.plan(session, constants, catalog, body, стекло, 1)
    автоматом = await craft.plan(session, constants, catalog, body, стекло, 1, auto=True)
    assert руками.quality > автоматом.quality, "ремесло адаптивно, станок — нет"

    #: И обратное, ради чего развилка честна: небрежный человек проигрывает
    #: станку. Станок ровен всегда — он не бывает ни лучше, ни хуже себя.
    proc = craft.procedure(catalog, стекло)
    мимо = {имя: доля * 3 for имя, доля in proc.per_unit.items()}
    небрежно = await craft.plan(
        session, constants, catalog, body, стекло, 1, proportions=мимо
    )
    assert небрежно.quality < автоматом.quality
    assert автоматом.accuracy == 1.0, "пропорция автомата — его настройка"


# --- энергия ----------------------------------------------------------------


async def test_автомат_ест_энергию_и_платит_по_тарифу(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Платит тот, кто жжёт: иначе энергетика — дотация, а не экономика."""
    цех, identity, body = await _цех(session)
    pool = await _пул(session, constants, цех)
    счёт = await _денег(session, identity)
    было_в_пуле = float(pool.stored)
    было_денег = await ledger.balance(session, счёт.id)

    план = await craft.plan(session, constants, catalog, body, NAILS, 2, auto=True)
    часов = план.minutes / MINUTES_PER_HOUR
    assert план.energy == pytest.approx(
        constants[R.ENERGY_AUTO_BENCH_DRAW] * часов
    )

    await craft.start(session, constants, catalog, body, NAILS, 2, auto=True)
    assert float(pool.stored) == pytest.approx(было_в_пуле - план.energy)

    казна = await ledger.account_for(
        session, AccountKind.CITY_TREASURY, pool.node_id
    )
    уплачено = было_денег - await ledger.balance(session, счёт.id)
    assert уплачено == план.energy_cost > 0
    assert await ledger.balance(session, казна.id) == уплачено


async def test_ручная_партия_энергии_не_ест(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Ремесло остаётся доступным тому, у кого нет денег на счета (D-135)."""
    цех, identity, body = await _цех(session, автомат=False)
    pool = await _пул(session, constants, цех)
    было = float(pool.stored)

    план = await craft.plan(session, constants, catalog, body, NAILS, 2)
    assert план.energy == 0 and план.energy_cost == 0
    await craft.start(session, constants, catalog, body, NAILS, 2)
    assert float(pool.stored) == pytest.approx(было)


async def test_пустой_пул_останавливает_промышленность(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Город без топлива стоит — и это видно всем, а не одному владельцу."""
    цех, identity, body = await _цех(session)
    await _пул(session, constants, цех, сколько=0)
    await _денег(session, identity)

    with pytest.raises(energy.NotEnough):
        await craft.start(session, constants, catalog, body, NAILS, 2, auto=True)


async def test_вне_города_автомат_не_работает(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Вне городской территории сети нет: промышленность живёт в городе."""
    метка = uuid.uuid4().hex[:6]
    хутор = await world.create_node(
        session, f"terra.lone.{метка}", "Хутор", area_m2=100, layer=Layer.PLANET
    )
    двор = await world.node_container(session, хутор)
    await world.grant_item(session, двор, craft.AUTO_BENCH, quality=60, origin="тест")
    identity = await world.create_identity(session, f"Одиночка-{метка}")
    body = await world.print_body(session, identity, хутор)
    await world.learn(session, identity, NAILS)
    карман = await world.body_container(session, body)
    await world.grant_item(session, карман, INGOT, amount=50, quality=70, origin="тест")
    await _денег(session, identity)

    with pytest.raises(energy.NoGrid):
        await craft.start(session, constants, catalog, body, NAILS, 1, auto=True)
