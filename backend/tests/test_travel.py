"""Переход между узлами (D-045, D-107).

Проверяется то, ради чего карта — граф, а не сетка:

* по прямой не ходят: нет ребра — нет пути;
* покрытие решает время, и бездорожье дороже дороги;
* переход занимает время и приходит **заданием журнала**, а не проверкой при
  чтении: закрыл вкладку — всё равно придёшь;
* пока идёшь, тебя нет: присутственное закрыто всё до одного.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import craft, jobs, market, mining, travel, world
from src.models.identity import Body
from src.models.travel import TravelState
from src.models.world import Surface


async def _два_узла(session: AsyncSession, *, surface: Surface = Surface.ROAD, секунд=30):
    метка = uuid.uuid4().hex[:8]
    здесь = await world.create_node(session, f"terra.here.{метка}", "Здесь", area_m2=100)
    там = await world.create_node(session, f"terra.there.{метка}", "Там", area_m2=100)
    await travel.connect(session, здесь, там, base_seconds=секунд, surface=surface)
    identity = await world.create_identity(session, f"Ходок-{метка}")
    body = await world.print_body(session, identity, здесь)
    return здесь, там, body


# --- граф -------------------------------------------------------------------


async def test_по_прямой_не_ходят(session: AsyncSession, constants: Constants) -> None:
    """Ребра нет — пути нет. На этом держатся мосты, перевалы и засады."""
    здесь, _, body = await _два_узла(session)
    далеко = await world.create_node(session, "terra.faraway", "Далеко", area_m2=100)
    with pytest.raises(travel.NoEdge):
        await travel.depart(session, constants, body, далеко)


async def test_ребро_ненаправленное(session: AsyncSession, constants: Constants) -> None:
    """Дорога одинакова в обе стороны, и второй строки для этого не нужно."""
    здесь, там, _ = await _два_узла(session)
    отсюда = await travel.exits(session, constants, здесь)
    оттуда = await travel.exits(session, constants, там)
    assert [путь.key for путь in отсюда] == [там.key]
    assert [путь.key for путь in оттуда] == [здесь.key]


async def test_бездорожье_дольше_дороги(session: AsyncSession, constants: Constants) -> None:
    """Покрытие решает и время, и саму возможность проехать (D-107)."""
    _, _, тело_по_дороге = await _два_узла(session, surface=Surface.ROAD)
    дорога = (await travel.exits(session, constants, await _узел(session, тело_по_дороге)))[0]

    _, _, тело_по_бездорожью = await _два_узла(session, surface=Surface.TRAIL)
    тропа = (await travel.exits(session, constants, await _узел(session, тело_по_бездорожью)))[0]

    _, _, тело_по_тракту = await _два_узла(session, surface=Surface.PAVED)
    тракт = (await travel.exits(session, constants, await _узел(session, тело_по_тракту)))[0]

    assert тропа.seconds > дорога.seconds > тракт.seconds
    assert тропа.seconds == pytest.approx(дорога.seconds * constants[R.ROAD_TRAIL_MULTIPLIER])


# --- переход ----------------------------------------------------------------


async def test_переход_занимает_время(session: AsyncSession, constants: Constants) -> None:
    """Дорога стоит времени — иначе география исчезает вместе с перевозчиком."""
    здесь, там, body = await _два_узла(session, секунд=30)
    переход = await travel.depart(session, constants, body, там)
    await session.commit()

    assert переход.arrives_at > переход.started_at
    assert body.node_id == здесь.id, "тело ещё не там: телепорта нет"
    assert переход.state is TravelState.GOING


async def test_приход_случается_заданием(
    factory: async_sessionmaker[AsyncSession], constants: Constants
) -> None:
    """Закрыл вкладку — всё равно придёшь: приход обязан быть в журнале."""
    async with factory() as session, session.begin():
        _, там, body = await _два_узла(session, секунд=30)
        переход = await travel.depart(session, constants, body, там)
        срок, body_id, там_id = переход.arrives_at, body.id, там.id

    #: До срока задание не берётся.
    assert await jobs.run_one(factory, now=срок - timedelta(seconds=5)) is None
    задание = await jobs.run_one(factory, now=срок)
    assert задание is not None and задание.kind == "travel.leg"

    async with factory() as session:
        body = await session.get(Body, body_id)
        assert body.node_id == там_id
        going = await travel.current(session, body)
        assert going is None, "переход закрыт"


async def test_автопуть_строит_маршрут_и_идёт_сам(
    factory: async_sessionmaker[AsyncSession], constants: Constants
) -> None:
    """Клик в дальний узел: первый отрезок выходится сейчас, хвост — планом,
    и приход каждого отрезка сам выводит тело в следующий (D-045)."""
    async with factory() as session, session.begin():
        метка = uuid.uuid4().hex[:8]
        a = await world.create_node(session, f"terra.pa.{метка}", "А", area_m2=100)
        b = await world.create_node(session, f"terra.pb.{метка}", "Б", area_m2=100)
        c = await world.create_node(session, f"terra.pc.{метка}", "В", area_m2=100)
        await travel.connect(session, a, b, base_seconds=30)
        await travel.connect(session, b, c, base_seconds=30)
        identity = await world.create_identity(session, f"Путник-{метка}")
        body = await world.print_body(session, identity, a)

        переход = await travel.depart(session, constants, body, c)
        assert переход.to_node_id == b.id, "первый отрезок — в соседа по маршруту"
        assert переход.plan == [str(c.id)], "хвост маршрута лежит планом"
        срок1, body_id, b_id, c_id = переход.arrives_at, body.id, b.id, c.id

    #: Первый отрезок пришёл — второй вышел сам, без клика.
    задание = await jobs.run_one(factory, now=срок1)
    assert задание is not None and задание.kind == "travel.leg"

    async with factory() as session:
        body = await session.get(Body, body_id)
        assert body.node_id == b_id, "тело в промежуточном узле"
        дальше = await travel.current(session, body)
        assert дальше is not None and дальше.to_node_id == c_id
        assert not дальше.plan, "последний отрезок — без хвоста"
        срок2 = дальше.arrives_at

    задание = await jobs.run_one(factory, now=срок2)
    assert задание is not None

    async with factory() as session:
        body = await session.get(Body, body_id)
        assert body.node_id == c_id, "дошёл до конца маршрута"
        assert await travel.current(session, body) is None


async def test_автопуть_выбирает_быстрейший_путь(
    session: AsyncSession, constants: Constants
) -> None:
    """Маршрут считается по времени с покрытием, а не по числу узлов."""
    метка = uuid.uuid4().hex[:8]
    a = await world.create_node(session, f"terra.qa.{метка}", "А", area_m2=100)
    b = await world.create_node(session, f"terra.qb.{метка}", "Б", area_m2=100)
    c = await world.create_node(session, f"terra.qc.{метка}", "В", area_m2=100)
    d = await world.create_node(session, f"terra.qd.{метка}", "Г", area_m2=100)
    #: Два пути в d: длинный «прямой» через c и короткий крюк через b.
    await travel.connect(session, a, c, base_seconds=500, surface=Surface.TRAIL)
    await travel.connect(session, c, d, base_seconds=500, surface=Surface.TRAIL)
    await travel.connect(session, a, b, base_seconds=30, surface=Surface.PAVED)
    await travel.connect(session, b, d, base_seconds=30, surface=Surface.PAVED)

    легло = await travel.route(session, constants, a.id, d.id)
    assert легло == [b.id, d.id], "быстрый тракт бьёт короткое бездорожье"


async def test_автопуть_в_несвязанный_узел_отказывает(
    session: AsyncSession, constants: Constants
) -> None:
    _, _, body = await _два_узла(session)
    остров = await world.create_node(
        session, f"terra.isle.{uuid.uuid4().hex[:6]}", "Остров", area_m2=100
    )
    with pytest.raises(travel.NoRoute):
        await travel.depart(session, constants, body, остров)


async def test_в_двух_дорогах_сразу_не_ходят(
    session: AsyncSession, constants: Constants
) -> None:
    _, там, body = await _два_узла(session)
    await travel.depart(session, constants, body, там)
    with pytest.raises(travel.AlreadyGoing):
        await travel.depart(session, constants, body, там)


# --- пока идёшь, тебя нет ---------------------------------------------------


async def test_в_пути_не_добывают(session: AsyncSession, constants: Constants) -> None:
    """Материя требует присутствия, а присутствия сейчас нет (D-044)."""
    здесь, там, body = await _два_узла(session)
    жила = await world.create_vein(session, здесь, "Железная руда", richness=60, remaining=1000)
    await travel.depart(session, constants, body, там)

    with pytest.raises(travel.InTransit):
        await mining.start(session, constants, body, жила)


async def test_в_пути_не_грузят_и_не_покупают(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    здесь, там, body = await _два_узла(session)
    двор = await world.node_container(session, здесь)
    await world.grant_item(session, двор, market.TERMINAL, quality=70, origin="тест")
    карман = await world.body_container(session, body)
    await world.grant_item(session, карман, "Железная руда", amount=5, quality=60, origin="тест")

    await travel.depart(session, constants, body, там)
    with pytest.raises(travel.InTransit):
        await market.load(session, constants, body, "Железная руда", 5)


async def test_в_пути_не_копируют_рецепт(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Библиотека не работает удалённо — и на полпути к ней тоже (D-053)."""
    здесь, там, body = await _два_узла(session)
    здесь.properties = {"library": True}
    await session.flush()

    await travel.depart(session, constants, body, там)
    with pytest.raises(travel.InTransit):
        await craft.copy_recipe(session, catalog, body, "Гвозди")


async def test_в_пути_не_запускают_партию(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    здесь, там, body = await _два_узла(session)
    двор = await world.node_container(session, здесь)
    await world.grant_item(session, двор, "Верстак", quality=60, origin="тест")
    identity_id = body.identity_id
    from src.models.identity import Identity

    identity = await session.get(Identity, identity_id)
    await world.learn(session, identity, "Гвозди")
    карман = await world.body_container(session, body)
    await world.grant_item(session, карман, "Слиток железа", amount=5, quality=60, origin="тест")

    await travel.depart(session, constants, body, там)
    with pytest.raises(travel.InTransit):
        await craft.plan(session, constants, catalog, body, "Гвозди", 1)


# --- дорога стоит выносливости (D-147) --------------------------------------


async def test_дорога_стоит_выносливости(
    session: AsyncSession, constants: Constants
) -> None:
    """Время — плохая цена: закрыл вкладку и пришёл. Вторую платит тело."""
    _, там, body = await _два_узла(session, секунд=3600)
    было = float(body.stamina)
    await travel.depart(session, constants, body, там)
    #: Час дороги — ровно ставка вольта за час. Расход идёт от времени, а не
    #: от числа переходов: иначе шаг по кварталу дороже перехода через степь.
    assert float(body.stamina) == pytest.approx(
        было - constants[R.TRAVEL_STAMINA_PER_HOUR]
    )


async def test_шаг_по_городу_почти_ничего_не_стоит(
    session: AsyncSession, constants: Constants
) -> None:
    """Секунды дороги — доли единицы: география не наказывает за шаг."""
    _, там, body = await _два_узла(session, секунд=6)
    было = float(body.stamina)
    await travel.depart(session, constants, body, там)
    потрачено = было - float(body.stamina)
    assert 0 < потрачено < constants[R.TRAVEL_STAMINA_PER_HOUR]


async def test_без_сил_не_выходят(
    session: AsyncSession, constants: Constants
) -> None:
    """Выйти в дорогу, на которую не хватает сил, нельзя — как и начать партию."""
    from decimal import Decimal

    _, там, body = await _два_узла(session, секунд=36_000)
    body.stamina = Decimal("1")
    await session.flush()
    with pytest.raises(travel.NoStrength):
        await travel.depart(session, constants, body, там)
    assert await travel.current(session, body) is None, "отказ не оставляет перехода"


async def test_с_транспортом_дорога_телу_ничего_не_стоит(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Везёт транспорт, а не ноги (D-147, D-129).

    Транспорт при этом **впряжён**, а не лежит в кармане: тачка тяжелее предела
    носимого, и в руках её не бывает вовсе (D-157).
    """
    from src.engine import transport

    здесь, там, body = await _два_узла(session, секунд=3600)
    двор = await world.node_container(session, здесь)
    тачка = await world.grant_item(session, двор, "Тачка", quality=60, origin="тест")
    await transport.harness(session, constants, catalog, body, тачка)

    было = float(body.stamina)
    await travel.depart(session, constants, body, там)
    assert float(body.stamina) == pytest.approx(было)


async def test_цена_дороги_видна_до_выхода(constants: Constants) -> None:
    """Игрок обязан видеть цену до решения, а не после."""
    час = 3600
    пешком = travel.stamina_cost(constants, час, transport=False)
    с_повозкой = travel.stamina_cost(constants, час, transport=True)
    assert пешком == pytest.approx(constants[R.TRAVEL_STAMINA_PER_HOUR])
    assert с_повозкой == pytest.approx(
        пешком * constants[R.TRANSPORT_STAMINA_K]
    )


async def _узел(session: AsyncSession, body: Body):
    from src.models.world import Node

    node = await session.get(Node, body.node_id)
    assert node is not None
    return node
