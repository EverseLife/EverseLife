"""Крафт и качество (D-092, D-133).

Проверяется не «функция считает число», а то, ради чего система написана:

* количества входов берутся **из данных**, а не из головы (D-133);
* прогноз качества показан точным числом до того, как потрачены материалы;
* потолок задаёт самое слабое звено — станок или инструмент;
* у смеси пропорция зависит от качества сырья, у сборки её нет вовсе;
* партия идёт заданием журнала и завершается ровно один раз;
* материя не создаётся: вход списан, потери ушли в сток.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import craft, jobs, wear, world
from src.engine.craft import Procedure
from src.models.craft import BatchState, CraftBatch
from src.models.identity import Body, KnowledgeKind
from src.models.inventory import Item
from src.units import amount_float

#: Первый настоящий передел лестницы: слиток железа плавится операцией, без
#: рецепта (20-systems/03-crafting), а гвозди уже требуют знания.
INGOT = "Слиток железа"
NAILS = "Гвозди"
STEEL = "Сталь"
BENCH = "Верстак"
FORGE = "Кузница"
FURNACE = "Плавильная печь"


async def _мастерская(
    session: AsyncSession,
    *,
    #: Гвозди куются: пример-рецепт тестов живёт в кузнице (ребаланс станков).
    станок: str | None = FORGE,
    качество_станка: float = 60,
    library: bool = False,
):
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session,
        f"terra.workshop.{метка}",
        "Мастерская",
        area_m2=100,
        properties={"library": library},
    )
    identity = await world.create_identity(session, f"Мастер-{метка}")
    body = await world.print_body(session, identity, node)
    if станок is not None:
        двор = await world.node_container(session, node)
        await world.grant_item(
            session, двор, станок, quality=качество_станка, origin="сценарий теста"
        )
    return node, identity, body


async def _дать(session: AsyncSession, body, type_key: str, количество: float, качество: float):
    контейнер = await world.body_container(session, body)
    return await world.grant_item(
        session,
        контейнер,
        type_key,
        amount=количество,
        quality=качество,
        origin="сценарий теста",
    )


async def _в_инвентаре(session: AsyncSession, body, type_key: str) -> float:
    контейнер = await world.body_container(session, body)
    итог = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == контейнер.id, Item.type_key == type_key
        )
    )
    return amount_float(int(итог or 0))


# --- способ изготовления ----------------------------------------------------


def test_количества_входов_берутся_из_данных(catalog: Catalog) -> None:
    """Из головы количества не берутся никогда (D-133)."""
    рецепт = catalog.recipes.recipe(NAILS)
    способ = craft.procedure(catalog, NAILS)
    assert способ.per_unit == {INGOT: рецепт.amounts[INGOT]}
    assert способ.needs_recipe, "рецепт требует знания"


def test_операция_без_рецепта_знания_не_требует(catalog: Catalog) -> None:
    """Плавка — граница между «добыл» и «сделал», и она открыта всем."""
    способ = craft.procedure(catalog, INGOT)
    assert not способ.needs_recipe
    assert способ.station == FURNACE
    assert set(способ.per_unit) == {"Железная руда", "Уголь"}


def test_добыча_крафтом_не_притворяется(catalog: Catalog) -> None:
    """Операция, ничего не расходующая, берёт материю из мира — это не крафт."""
    with pytest.raises(craft.Unmakeable):
        craft.procedure(catalog, "Бревно")


def test_блюда_ждут_готовки(catalog: Catalog) -> None:
    """Роли приезжают вместе с готовкой на Э2 (D-119), и делать вид нельзя."""
    with pytest.raises(craft.Unmakeable):
        craft.procedure(catalog, "Похлёбка")


def test_время_растёт_с_глубиной_передела(catalog: Catalog, constants: Constants) -> None:
    """Главная ручка ценности лестницы: чем глубже передел, тем дольше (D-133)."""
    гвозди = craft.step_hours(catalog, catalog.recipes.recipe(NAILS))
    кирка = craft.step_hours(catalog, catalog.recipes.recipe("Стальная кирка"))
    assert кирка > гвозди > 0

    #: Собственный шаг первого передела — это и есть `craft.time_per_unit`.
    верёвка = craft.step_hours(catalog, catalog.recipes.recipe("Верёвка"))
    assert верёвка * 60 == pytest.approx(constants[R.CRAFT_TIME_PER_UNIT], rel=0.05)


def test_разбитый_станок_работает_медленно(catalog: Catalog, constants: Constants) -> None:
    способ = craft.procedure(catalog, NAILS)
    шкала = constants[R.QUALITY_SCALE]
    быстро = craft.batch_minutes(constants, способ, 1, шкала.max)
    медленно = craft.batch_minutes(constants, способ, 1, шкала.min)
    k = constants[R.CRAFT_STATION_SPEED_K]
    assert медленно / быстро == pytest.approx(k.max / k.min)


# --- качество ---------------------------------------------------------------


def _смесь(catalog: Catalog) -> Procedure:
    """Сталь: чугун, уголь, известняк — пятнадцать смесей из ста двенадцати."""
    return craft.procedure(catalog, STEEL)


def test_бедной_руде_нужно_больше_флюса(constants: Constants, catalog: Catalog) -> None:
    """У смеси оптимум зависит от качества сырья (D-092)."""
    смесь = _смесь(catalog)
    добавка = смесь.inputs[1]
    бедная = craft.optimal_amounts(constants, смесь, 1, 20)
    чистая = craft.optimal_amounts(constants, смесь, 1, 90)
    assert бедная[добавка] > чистая[добавка]
    #: Основы это не касается: она и есть то, что переделывают.
    assert бедная[смесь.inputs[0]] == чистая[смесь.inputs[0]]


def test_у_сборки_пропорций_нет(constants: Constants, catalog: Catalog) -> None:
    """Верстак — это бревно и верёвка, третьего не дано (D-092)."""
    сборка = craft.procedure(catalog, NAILS)
    для_бедного = craft.optimal_amounts(constants, сборка, 1, 10)
    для_чистого = craft.optimal_amounts(constants, сборка, 1, 90)
    assert для_бедного == для_чистого


def test_промах_дороже_и_разброснее(constants: Constants, catalog: Catalog) -> None:
    """Плохая плавка съедает больше и даёт непредсказуемый слиток."""
    точно = craft.waste_share(constants, 1.0)
    мимо = craft.waste_share(constants, 0.0)
    assert точно == constants[R.CRAFT_WASTE_SHARE]
    assert мимо == constants[R.CRAFT_WASTE_BAD_RATIO]
    assert craft.spread_of(constants, 1.0) < craft.spread_of(constants, 0.0)


def test_потолок_задаёт_слабое_звено(constants: Constants, catalog: Catalog) -> None:
    """Отличный инструмент на разбитой наковальне даст посредственный результат."""
    сборка = craft.procedure(catalog, NAILS)
    шкала = constants[R.QUALITY_SCALE]
    отличное = craft.forecast_quality(
        constants, сборка, ceiling=шкала.max, material=шкала.max, accuracy=1.0
    )
    посредственное = craft.forecast_quality(
        constants, сборка, ceiling=шкала.mid, material=шкала.max, accuracy=1.0
    )
    assert отличное == pytest.approx(шкала.max)
    assert посредственное == pytest.approx(шкала.mid)


def test_премия_ремесла_только_у_смеси(constants: Constants, catalog: Catalog) -> None:
    """Мастер превосходит станок адаптивностью, а не большой цифрой (D-058)."""
    шкала = constants[R.QUALITY_SCALE]
    смесь = _смесь(catalog)
    сборка = craft.procedure(catalog, NAILS)

    у_смеси = craft.forecast_quality(
        constants, смесь, ceiling=шкала.mid, material=шкала.max, accuracy=1.0
    )
    у_сборки = craft.forecast_quality(
        constants, сборка, ceiling=шкала.mid, material=шкала.max, accuracy=1.0
    )
    assert у_смеси > шкала.mid, "точная пропорция поднимает выше потолка станка"
    assert у_сборки == pytest.approx(шкала.mid), "у сборки прибавке взяться неоткуда"
    assert у_смеси - шкала.mid <= constants[R.QUALITY_HAND_CRAFT_BONUS]


# --- партия -----------------------------------------------------------------


async def test_прогноз_показан_числом_до_партии(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Без точного числа игрок не свяжет действие с результатом (D-092)."""
    _, identity, body = await _мастерская(session)
    await world.learn(session, identity, NAILS)
    await _дать(session, body, INGOT, 10, качество=80)

    прогноз = await craft.plan(session, constants, catalog, body, NAILS, 3)
    assert isinstance(прогноз.quality, float)
    assert прогноз.minutes > 0
    assert прогноз.consumes[INGOT] > 0

    #: Прогноз ничего не тратит.
    assert await _в_инвентаре(session, body, INGOT) == pytest.approx(10)


async def test_без_рецепта_партия_не_идёт(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Игрок с рождения не умеет создавать ничего."""
    _, _, body = await _мастерская(session)
    await _дать(session, body, INGOT, 10, качество=80)
    with pytest.raises(craft.NotLearned):
        await craft.plan(session, constants, catalog, body, NAILS, 1)


async def test_без_станка_в_узле_партия_не_идёт(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Требование «станок в месте» и делает крафт градообразующим."""
    _, identity, body = await _мастерская(session, станок=None)
    await world.learn(session, identity, NAILS)
    await _дать(session, body, INGOT, 10, качество=80)
    with pytest.raises(craft.NoStation):
        await craft.plan(session, constants, catalog, body, NAILS, 1)


async def test_не_хватило_входов__партия_не_начнётся(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Материя не создаётся ни при каких обстоятельствах (И1)."""
    _, identity, body = await _мастерская(session)
    await world.learn(session, identity, NAILS)
    await _дать(session, body, INGOT, 1, качество=80)
    with pytest.raises(craft.NotEnough):
        await craft.start(session, constants, catalog, body, NAILS, 10)


async def test_партия_больше_потолка_не_ставится(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, identity, body = await _мастерская(session)
    await world.learn(session, identity, NAILS)
    сверх_меры = constants[R.CRAFT_BATCH_MAX] + 1
    with pytest.raises(craft.TooBig):
        await craft.plan(session, constants, catalog, body, NAILS, сверх_меры)


async def test_вход_списывается_сразу_и_с_угаром(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Угар — сток С9: материи в мире стало меньше, чем было (D-129)."""
    _, identity, body = await _мастерская(session)
    await world.learn(session, identity, NAILS)
    await _дать(session, body, INGOT, 10, качество=60)

    норма = catalog.recipes.recipe(NAILS).amounts[INGOT] * 4
    партия = await craft.start(session, constants, catalog, body, NAILS, 4)
    await session.commit()

    осталось = await _в_инвентаре(session, body, INGOT)
    списано = 10 - осталось
    assert списано > норма, "потери берутся сверх нормы, а не из выхода"
    assert партия.state is BatchState.RUNNING
    assert партия.ready_at > партия.started_at


async def test_партия_приходит_заданием_и_ровно_один_раз(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Эффект партии обязан быть в базе, а задание — идемпотентным."""
    async with factory() as session, session.begin():
        _, identity, body = await _мастерская(session)
        await world.learn(session, identity, NAILS)
        await _дать(session, body, INGOT, 10, качество=70)
        партия = await craft.start(session, constants, catalog, body, NAILS, 2)
        готова, batch_id, body_id = партия.ready_at, партия.id, body.id

    #: Время партии ещё не пришло — задание не берётся.
    assert await jobs.run_one(factory, now=готова - timedelta(minutes=1)) is None

    задание = await jobs.run_one(factory, now=готова)
    assert задание is not None and задание.kind == "craft.batch"

    async with factory() as session:
        тело = await session.get(Body, body_id)
        assert тело is not None
        карман = await world.body_container(session, тело)
        сделано = (
            await session.execute(
                select(Item).where(Item.container_id == карман.id, Item.type_key == NAILS)
            )
        ).scalars().all()
        assert len(сделано) == 1, "гвозди складываются: одна стопка"
        assert amount_float(сделано[0].amount) == pytest.approx(2)
        assert сделано[0].maker_identity_id is not None, "клеймо обязательно (D-058)"

        партия = await session.get(CraftBatch, batch_id)
        assert партия is not None and партия.state is BatchState.DONE

        #: Повтор того же задания второй партии не даёт.
        await craft.finish(session, задание)
        всё = (
            await session.execute(
                select(func.count())
                .select_from(Item)
                .where(Item.container_id == карман.id, Item.type_key == NAILS)
            )
        ).scalar_one()
        assert всё == 1


async def test_станок_рецепта_ищется_через_синонимы(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """В рецептах станок зовётся «Печью», а в узле стоит «Плавильная печь».

    Без разрешения синонима вся химия и аффинаж были неизготовимы: движок
    искал станок с именем, которого в мире не бывает.
    """
    ГЛАЗУРЬ = "Стекло"
    _, identity, body = await _мастерская(session, станок=FURNACE, качество_станка=70)
    await world.learn(session, identity, ГЛАЗУРЬ)
    for сырьё in ("Кварцевый песок", "Уголь"):
        await _дать(session, body, сырьё, 100, качество=60)

    план = await craft.plan(session, constants, catalog, body, ГЛАЗУРЬ, 1)
    assert план.ceiling == pytest.approx(70), "потолок задала стоящая в узле печь"


async def test_партия_плавки_доходит_до_конца(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Выход операции рецептом не описан — и партия обязана это пережить.

    Плавка идёт без рецепта (20-systems/03), поэтому справочник о слитке ничего
    не знает. Задание, спрашивавшее у него съедобность, роняло всю партию:
    вход списан, изделия нет.
    """
    async with factory() as session, session.begin():
        _, _, body = await _мастерская(session, станок=FURNACE)
        await _дать(session, body, "Железная руда", 20, качество=60)
        await _дать(session, body, "Уголь", 20, качество=60)
        партия = await craft.start(session, constants, catalog, body, INGOT, 1)
        готова, body_id = партия.ready_at, body.id

    задание = await jobs.run_one(factory, now=готова)
    assert задание is not None and задание.kind == "craft.batch"

    async with factory() as session:
        тело = await session.get(Body, body_id)
        карман = await world.body_container(session, тело)
        слитки = (
            await session.execute(
                select(Item).where(Item.container_id == карман.id, Item.type_key == INGOT)
            )
        ).scalars().all()
        assert слитки, "слиток вышел из печи, а не сгинул вместе с заданием"


async def test_ушёл_от_станка__сделанное_осталось_у_станка(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Материя не телепортируется за мастером и не исчезает вместе с ним."""
    async with factory() as session, session.begin():
        node, identity, body = await _мастерская(session)
        await world.learn(session, identity, NAILS)
        await _дать(session, body, INGOT, 10, качество=70)
        партия = await craft.start(session, constants, catalog, body, NAILS, 2)
        готова, node_id = партия.ready_at, node.id

        далеко = await world.create_node(
            session, f"terra.away.{uuid.uuid4().hex[:8]}", "Далеко", area_m2=100
        )
        body.node_id = далеко.id

    await jobs.run_one(factory, now=готова)

    async with factory() as session:
        двор = await world.node_container(session, await _узел(session, node_id))
        сделано = (
            await session.execute(
                select(Item).where(Item.container_id == двор.id, Item.type_key == NAILS)
            )
        ).scalars().all()
        assert len(сделано) == 1


async def test_изделия_не_складываются_и_каждое_со_своим_качеством(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Сырьё складывается, изделия нет (04-items)."""
    async with factory() as session, session.begin():
        _, identity, body = await _мастерская(session, станок=FORGE, качество_станка=80)
        await world.learn(session, identity, "Железная кирка")
        await _дать(session, body, INGOT, 20, качество=70)
        await _дать(session, body, "Рукоять", 20, качество=70)
        партия = await craft.start(session, constants, catalog, body, "Железная кирка", 3)
        готова, body_id = партия.ready_at, body.id

    await jobs.run_one(factory, now=готова)

    async with factory() as session:
        тело = await session.get(Body, body_id)
        assert тело is not None
        карман = await world.body_container(session, тело)
        кирки = (
            await session.execute(
                select(Item).where(
                    Item.container_id == карман.id, Item.type_key == "Железная кирка"
                )
            )
        ).scalars().all()
        assert len(кирки) == 3, "каждая кирка — отдельная вещь со своим клеймом"
        assert all(amount_float(кирка.amount) == 1 for кирка in кирки)


async def test_станок_изнашивается_за_партию(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Содержание обязательно: станок конечен, как и всё прочее (D-129)."""
    async with factory() as session, session.begin():
        node, identity, body = await _мастерская(session)
        await world.learn(session, identity, NAILS)
        await _дать(session, body, INGOT, 10, качество=70)
        партия = await craft.start(session, constants, catalog, body, NAILS, 2)
        готова, станок_id = партия.ready_at, партия.station_item_id

    await jobs.run_one(factory, now=готова)

    async with factory() as session:
        станок = await session.get(Item, станок_id)
        assert станок is not None
        #: Износ делится на срок службы: станок получше и служит дольше.
        ожидалось = constants[R.WEAR_STATION_PER_BATCH] / wear.life_factor(constants, 60)
        assert float(станок.condition) == pytest.approx(100 - ожидалось, abs=0.01)


async def test_результат_держится_в_обещанном_разбросе(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Крафт не игровой автомат: обещанное число и есть середина результата."""
    async with factory() as session, session.begin():
        _, identity, body = await _мастерская(session)
        await world.learn(session, identity, NAILS)
        await _дать(session, body, INGOT, 10, качество=70)
        партия = await craft.start(session, constants, catalog, body, NAILS, 2)
        обещано, разброс = float(партия.quality), float(партия.spread)
        готова, body_id = партия.ready_at, body.id

    await jobs.run_one(factory, now=готова)

    async with factory() as session:
        тело = await session.get(Body, body_id)
        assert тело is not None
        карман = await world.body_container(session, тело)
        гвозди = (
            await session.execute(
                select(Item).where(Item.container_id == карман.id, Item.type_key == NAILS)
            )
        ).scalars().first()
        assert гвозди is not None and гвозди.quality is not None
        assert abs(float(гвозди.quality) - обещано) <= разброс + 0.01


async def test_библиотека_не_работает_удалённо(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Единственное ограничение Библиотеки — географическое (D-053)."""
    _, identity, body = await _мастерская(session, library=False)
    with pytest.raises(craft.NoLibrary):
        await craft.copy_recipe(session, catalog, body, NAILS)

    _, identity, body = await _мастерская(session, library=True)
    знание = await craft.copy_recipe(session, catalog, body, NAILS)
    await session.commit()
    assert знание is not None
    assert знание.kind is KnowledgeKind.RECIPE and знание.key == NAILS


async def test_копирование_рецепта_стоит_выносливости(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Знание бесплатно деньгами, но переписать его — работа (D-148).

    Плата телом сохраняет и бесплатность знания, и его цену: за день можно
    унести десяток рецептов, а не весь список.
    """
    from decimal import Decimal

    _, identity, body = await _мастерская(session, library=True)
    было = float(body.stamina)
    await craft.copy_recipe(session, catalog, body, NAILS)
    assert float(body.stamina) == pytest.approx(
        было - constants[R.CRAFT_COPY_STAMINA]
    )

    #: Уже известное не переписывают: за одно и то же тело не платит дважды.
    сейчас = float(body.stamina)
    assert await craft.copy_recipe(session, catalog, body, NAILS) is None
    assert float(body.stamina) == pytest.approx(сейчас)

    body.stamina = Decimal("1")
    await session.flush()
    with pytest.raises(craft.NoStrength):
        await craft.copy_recipe(session, catalog, body, "Брус")


async def _узел(session: AsyncSession, node_id: uuid.UUID):
    from src.models.world import Node

    node = await session.get(Node, node_id)
    assert node is not None
    return node
