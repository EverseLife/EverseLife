"""Земледелие делянками (D-118, D-105).

Проверяется то, ради чего система устроена именно так:

* земля конечна: сумма делянок не больше площади узла;
* цикл честный: не вспахано — не посеешь, не дозрело — не уберёшь;
* небрежность режет урожай на `farm.neglect_penalty` за сутки, но не обнуляет;
* монокультура истощает, бобы возвращают, пар лечит по времени;
* перекройка границ не лечит землю: наследование при делении и слиянии;
* у реки поливают из реки, в сухом месте воду носят руками.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import farm, jobs, world
from src.models.farm import Plot, PlotState
from src.models.inventory import Item
from src.units import amount_float

SPELT = "spelt"
BEANS = "beans"


async def _хутор(session: AsyncSession, *, вода: str = "река", плодородие: float = 55,
                 area: float = 200):
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session, f"terra.farm.{метка}", "Хутор", area_m2=area,
        properties={"вода": вода, "плодородие": плодородие},
    )
    identity = await world.create_identity(session, f"Фермер-{метка}")
    body = await world.print_body(session, identity, node)
    #: Хозяйство ведёт владелец: фермер фикстуры свой участок уже занял.
    node.owner_identity_id = identity.id
    await session.flush()
    return node, identity, body


async def _зерно(session: AsyncSession, body, каталог: Catalog, culture: str, сколько=200):
    """Семенной фонд базового сорта: сеют семенами, а не урожаем (D-057)."""
    from src.engine import breed
    from src.units import PERCENT

    сорт = await breed.landrace(session, каталог, culture)
    карман = await world.body_container(session, body)
    return await breed.seed_lot(
        session, каталог, карман.id, сорт, сколько, PERCENT
    )


async def _готовая(session, constants, catalog, body, *, area=10.0, culture=SPELT):
    """Делянка, доведённая до посева, минуя ожидание пахоты."""
    plot = await farm.mark(session, constants, body, name="грядка", area=area)
    plot.state = PlotState.PLOWED
    await session.flush()
    семена = await _зерно(session, body, catalog, culture)
    return await farm.sow(session, constants, catalog, body, plot, семена)


def _день(constants: Constants) -> timedelta:
    return timedelta(hours=constants[R.TIME_DAY_TERRA])


# --- земля ------------------------------------------------------------------


async def test_земля_узла_конечна(session: AsyncSession, constants: Constants) -> None:
    _, _, body = await _хутор(session, area=20)
    await farm.mark(session, constants, body, name="первая", area=15)
    with pytest.raises(farm.NoLand):
        await farm.mark(session, constants, body, name="вторая", area=10)


async def test_мельче_минимума_не_межуют(session: AsyncSession, constants: Constants) -> None:
    _, _, body = await _хутор(session)
    with pytest.raises(farm.TooSmall):
        await farm.mark(session, constants, body, name="лоскут",
                        area=constants[R.FARM_PLOT_MIN_AREA] - 1)


async def test_без_плодородия_земля_не_родит(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Плодородие — свойство места (D-126): нет свойства — нет урожая."""
    _, _, body = await _хутор(session, плодородие=0)
    plot = await _готовая(session, constants, catalog, body)
    plant = catalog.plants.by_id(SPELT)
    спелость = farm.ripe_at(constants, plot, plant)
    собрано = await farm.harvest(session, constants, catalog, body, plot, now=спелость)
    assert собрано == 0


# --- цикл -------------------------------------------------------------------


async def test_цикл_честный(session: AsyncSession, constants: Constants,
                            catalog: Catalog) -> None:
    _, _, body = await _хутор(session)
    plot = await farm.mark(session, constants, body, name="грядка", area=10)
    семена = await _зерно(session, body, catalog, SPELT)

    with pytest.raises(farm.WrongState):
        await farm.sow(session, constants, catalog, body, plot, семена)

    plot.state = PlotState.PLOWED
    await session.flush()
    await farm.sow(session, constants, catalog, body, plot, семена)

    with pytest.raises(farm.WrongState):
        #: Не дозрело — не уберёшь.
        await farm.harvest(session, constants, catalog, body, plot)


async def test_вспашка_идёт_заданием(
    factory: async_sessionmaker[AsyncSession], constants: Constants
) -> None:
    async with factory() as session, session.begin():
        _, _, body = await _хутор(session)
        plot = await farm.plow(
            session, constants, body,
            await farm.mark(session, constants, body, name="грядка", area=10),
        )
        assert plot.state is PlotState.PLOWING
        plot_id = plot.id

    задание = await jobs.run_one(factory, now=datetime.now(UTC) + timedelta(hours=1))
    assert задание is not None and задание.kind == "farm.plow"

    async with factory() as session:
        plot = await session.get(Plot, plot_id)
        assert plot.state is PlotState.PLOWED


async def test_посев_расходует_семена(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Семена — предмет со своей нормой высева на метр (D-057)."""
    _, _, body = await _хутор(session)
    plant = catalog.plants.by_id(SPELT)
    plot = await farm.mark(session, constants, body, name="грядка", area=10)
    plot.state = PlotState.PLOWED
    await session.flush()

    мало = await _зерно(session, body, catalog, SPELT, сколько=1)
    with pytest.raises(farm.NoSeeds):
        await farm.sow(session, constants, catalog, body, plot, мало)

    семена = await _зерно(session, body, catalog, SPELT, сколько=100)
    await farm.sow(session, constants, catalog, body, plot, семена)

    карман = await world.body_container(session, body)
    осталось = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == карман.id, Item.type_key == plant.seed
        )
    )
    #: Мешок из первой попытки остался нетронутым: партия не начинается,
    #: если семян не хватает.
    assert amount_float(int(осталось)) == pytest.approx(
        101 - constants[R.FARM_SEED_RATE] * 10
    )


async def test_урожай_из_формулы_вольта(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Площадь × выведенная урожайность × плодородие × уход — и ничего сверх."""
    _, _, body = await _хутор(session, плодородие=55)
    plant = catalog.plants.by_id(SPELT)
    plot = await _готовая(session, constants, catalog, body, area=10)

    #: Полный уход: обходим каждые сутки цикла.
    посеяно = plot.sown_at
    for день in range(int(plant.cycle_days)):
        await farm.care(session, constants, body, plot,
                        now=посеяно + _день(constants) * день)

    спелость = farm.ripe_at(constants, plot, plant)
    собрано = await farm.harvest(session, constants, catalog, body, plot, now=спелость)
    await session.commit()

    ожидание = 10 * plant.yield_per_m2 * (55 / plant.requires.fertility)
    assert собрано == pytest.approx(ожидание, rel=0.01)

    #: Собранная стопка — не мешок семян: ищем по качеству урожая, а оно
    #: равно плодородию, взятому по полному уходу.
    карман = await world.body_container(session, body)
    стопки = (
        await session.execute(
            select(Item).where(Item.container_id == карман.id, Item.type_key == plant.gives)
        )
    ).scalars().all()
    качества = {None if s.quality is None else float(s.quality) for s in стопки}
    assert 55.0 in качества, f"среди стопок нет урожая: {качества}"


async def test_небрежность_режет_но_не_обнуляет(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """За отпуск не наказывают: доля, а не ноль (D-118)."""
    _, _, body = await _хутор(session)
    plant = catalog.plants.by_id(SPELT)
    plot = await _готовая(session, constants, catalog, body, area=10)
    спелость = farm.ripe_at(constants, plot, plant)

    #: Ни одного обхода за весь цикл.
    заброшено = await farm.harvest(session, constants, catalog, body, plot, now=спелость)

    доля = 1 - constants[R.FARM_NEGLECT_PENALTY] * plant.cycle_days / 100
    полный = 10 * plant.yield_per_m2 * (55 / plant.requires.fertility)
    assert заброшено == pytest.approx(max(0.0, полный * доля), rel=0.01)


async def test_уход_суточный_а_не_почасовой(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _хутор(session)
    plot = await _готовая(session, constants, catalog, body)
    await farm.care(session, constants, body, plot, now=plot.sown_at)
    with pytest.raises(farm.WrongState):
        await farm.care(session, constants, body, plot,
                        now=plot.sown_at + timedelta(hours=1))


async def test_в_сухом_месте_воду_носят_руками(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """У реки — из реки; иначе вода — товар (D-126)."""
    _, _, body = await _хутор(session, вода="нет")
    plot = await _готовая(session, constants, catalog, body, area=10)

    with pytest.raises(farm.NoWater):
        await farm.care(session, constants, body, plot, now=plot.sown_at)

    карман = await world.body_container(session, body)
    await world.grant_item(session, карман, farm.WATER, amount=100, origin="тест")
    await farm.care(session, constants, body, plot, now=plot.sown_at)

    осталось = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == карман.id, Item.type_key == farm.WATER
        )
    )
    assert amount_float(int(осталось)) == pytest.approx(
        100 - constants[R.FARM_WATER_PER_M2] * 10
    )


# --- земля помнит -----------------------------------------------------------


async def test_монокультура_истощает_а_бобы_возвращают(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _хутор(session, плодородие=55)
    plant = catalog.plants.by_id(SPELT)
    plot = await _готовая(session, constants, catalog, body, area=10)
    момент = farm.ripe_at(constants, plot, plant)

    #: Первый цикл: культура сменилась (с «ничего»), истощения нет.
    await farm.harvest(session, constants, catalog, body, plot, now=момент)
    assert float(plot.fertility) == pytest.approx(55)

    #: Второй цикл той же культуры подряд — истощение.
    plot.state = PlotState.PLOWED
    plot.idle_since = None
    await session.flush()
    ещё = await _зерно(session, body, catalog, SPELT)
    await farm.sow(session, constants, catalog, body, plot, ещё, now=момент)
    момент = farm.ripe_at(constants, plot, plant)
    await farm.harvest(session, constants, catalog, body, plot, now=момент)
    assert float(plot.fertility) == pytest.approx(55 - constants[R.FARM_SOIL_DEPLETION])
    assert plot.same_culture_cycles == 2

    #: Бобы возвращают своё `restores_fertility` из данных.
    бобы = catalog.plants.by_id(BEANS)
    assert бобы.restores_fertility > 0, "иначе севообороту не на чем держаться"
    plot.state = PlotState.PLOWED
    plot.idle_since = None
    await session.flush()
    бобовые_семена = await _зерно(session, body, catalog, BEANS)
    await farm.sow(session, constants, catalog, body, plot, бобовые_семена, now=момент)
    момент = farm.ripe_at(constants, plot, бобы)
    было = float(plot.fertility)
    await farm.harvest(session, constants, catalog, body, plot, now=момент)
    assert float(plot.fertility) == pytest.approx(было + бобы.restores_fertility)
    assert plot.same_culture_cycles == 1


async def test_пар_лечит_по_времени(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Начисление по факту простоя: тик земле не нужен, как и сну."""
    _, _, body = await _хутор(session, плодородие=30)
    plot = await farm.mark(session, constants, body, name="пар", area=10)
    plot.fertility = 30
    await session.flush()

    двое_суток = datetime.now(UTC) + _день(constants) * 2
    await farm.plow(session, constants, body, plot, now=двое_суток)
    assert float(plot.fertility) == pytest.approx(
        30 + constants[R.FARM_FALLOW_RECOVERY] * 2, rel=0.01
    )


async def test_перекройка_не_лечит_землю(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Деление наследует как есть, слияние — взвешенно и с тяжёлой историей."""
    _, _, body = await _хутор(session, area=200, плодородие=50)
    plot = await farm.mark(session, constants, body, name="целое", area=100)
    plot.fertility = 20
    plot.last_culture = SPELT
    plot.same_culture_cycles = 3
    plot.idle_since = None
    await session.flush()

    кусок = await farm.split(session, constants, body, plot, 40, name="отрез")
    assert float(кусок.fertility) == pytest.approx(20), "деление не сбрасывает истощение"
    assert кусок.same_culture_cycles == 3
    assert float(plot.area_m2) == pytest.approx(60)

    #: Свежая делянка + истощённая: слияние взвешивает, история — тяжёлая.
    кусок.fertility = 80
    кусок.last_culture = None
    кусок.same_culture_cycles = 0
    кусок.idle_since = None
    await session.flush()
    целое = await farm.merge(session, constants, body, plot, кусок)
    assert float(целое.area_m2) == pytest.approx(100)
    assert float(целое.fertility) == pytest.approx((20 * 60 + 80 * 40) / 100)
    assert целое.last_culture == SPELT and целое.same_culture_cycles == 3


async def test_засеянное_не_перекраивают(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _хутор(session)
    plot = await _готовая(session, constants, catalog, body, area=20)
    with pytest.raises(farm.WrongState):
        await farm.split(session, constants, body, plot, 10, name="кусок")


async def test_чужую_делянку_не_трогают(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Наём — это доступ плюс доля, через договор (D-116), а не через кнопку."""
    node, _, хозяин = await _хутор(session)
    plot = await farm.mark(session, constants, хозяин, name="своя", area=10)

    гость = await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:6]}")
    тело_гостя = await world.print_body(session, гость, node)
    with pytest.raises(farm.NotYours):
        await farm.plow(session, constants, тело_гостя, plot)


async def test_сводка_считает_потери_в_день_набегания(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """«Минус половина урожая» видно сразу, а не сюрпризом при уборке.

    Числами — тому, кто знает агротехнику: без неё та же делянка показывает
    симптом, а не счёт потерь (D-057, проверяется в `test_agrotech`).
    """
    from src.models.identity import KnowledgeKind

    _, identity, body = await _хутор(session)
    await world.learn(session, identity, SPELT, kind=KnowledgeKind.AGROTECH)
    plot = await _готовая(session, constants, catalog, body)
    plot.sown_at = datetime.now(UTC) - _день(constants) * 2 - timedelta(hours=1)
    await session.flush()

    сводка = await farm.survey(session, constants, catalog, identity.id)
    assert len(сводка) == 1
    assert сводка[0]["missed_days"] == 2
    assert сводка[0]["asks_care"] is True


async def test_чужой_участок_не_межуют(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Хозяйство ведёт владелец: сначала займи землю (06-farming)."""
    node, _, _ = await _хутор(session)
    гость = await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:6]}")
    тело = await world.print_body(session, гость, node)
    with pytest.raises(farm.NotYours):
        await farm.mark(session, constants, тело, name="самозахват", area=10)


async def test_участок_занимают_ногами_и_один_раз(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Занять можно дикий узел; занятое и городское — нельзя."""
    метка = uuid.uuid4().hex[:8]
    дикий = await world.create_node(
        session, f"terra.wild.{метка}", "Дикий угол", area_m2=100,
        properties={"плодородие": 40},
    )
    первый = await world.create_identity(session, f"Первый-{метка}")
    тело = await world.print_body(session, первый, дикий)

    await world.claim_node(session, тело, дикий)
    assert дикий.owner_identity_id == первый.id
    #: Теперь разметка работает.
    await farm.mark(session, constants, тело, name="своя", area=10)

    второй = await world.create_identity(session, f"Второй-{метка}")
    тело2 = await world.print_body(session, второй, дикий)
    with pytest.raises(world.LandError):
        await world.claim_node(session, тело2, дикий)

    городской = await world.create_node(
        session, f"terra.town.{метка}", "Городская земля", area_m2=100,
    )
    городской.owner_city_id = uuid.uuid4()
    тело3 = await world.print_body(
        session, await world.create_identity(session, f"Третий-{метка}"), городской
    )
    with pytest.raises(world.LandError):
        await world.claim_node(session, тело3, городской)
