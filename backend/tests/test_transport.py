"""Транспорт и обоз (D-107, D-129, D-157).

Проверяется то, ради чего транспорт вообще введён:

* груз едет **в трюме**, а не в руках: предел носимого обходят повозкой, а не
  надетым рюкзаком;
* бездорожье транспорт не пускает вовсе — дорога есть предусловие торговли;
* обоз едет за телом сам и изнашивается за каждый отрезок, тем сильнее, чем
  полнее трюм;
* сломавшийся обоз встаёт, а груз остаётся лежать там, где встал.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import death, gear, jobs, transport, travel, world
from src.models.identity import Body
from src.models.inventory import Item
from src.models.travel import Harness
from src.models.world import Node, Surface

#: Что возим: сырьё с массой в килограмм за единицу (`inventory.mass_by_kind`).
ГРУЗ = "Железная руда"
ТЕЛЕГА = "Повозка"
ТАЧКА = "Тачка"


async def _обоз(
    session: AsyncSession,
    *,
    surface: Surface = Surface.ROAD,
    секунд: float = 600,
    транспорт: str = ТЕЛЕГА,
):
    """Два узла, тело и стоящий рядом транспорт."""
    метка = uuid.uuid4().hex[:8]
    здесь = await world.create_node(session, f"terra.tha.{метка}", "Здесь", area_m2=100)
    там = await world.create_node(session, f"terra.thb.{метка}", "Там", area_m2=100)
    await travel.connect(session, здесь, там, base_seconds=секунд, surface=surface)
    identity = await world.create_identity(session, f"Возчик-{метка}")
    body = await world.print_body(session, identity, здесь)
    двор = await world.node_container(session, здесь)
    телега = await world.grant_item(
        session, двор, транспорт, amount=1, origin="сценарий теста"
    )
    return здесь, там, body, телега


async def _в_руки(session: AsyncSession, body: Body, сколько: float) -> Item:
    """Положить груз в руки. В игре это несколько ходок: рука мала (D-146)."""
    карман = await world.body_container(session, body)
    return await world.grant_item(
        session, карман, ГРУЗ, amount=сколько, origin="сценарий теста"
    )


# --- упряжка ----------------------------------------------------------------


async def test_впрягаются_в_то_что_рядом(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body, телега = await _обоз(session)
    await transport.harness(session, constants, catalog, body, телега)
    впряжён = await transport.harnessed(session, body)
    assert впряжён is not None and впряжён.id == телега.id


async def test_в_мешок_зерна_не_впрягаются(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Транспорт — `kind: vehicle` из вольта, а не всё, что тяжёлое."""
    _, _, body, _ = await _обоз(session)
    мешок = await _в_руки(session, body, 1)
    with pytest.raises(transport.NotVehicle):
        await transport.harness(session, constants, catalog, body, мешок)


async def test_чужую_упряжку_не_перехватывают(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    здесь, _, body, телега = await _обоз(session)
    await transport.harness(session, constants, catalog, body, телега)

    сосед_id = await world.create_identity(session, f"Сосед-{uuid.uuid4().hex[:6]}")
    сосед = await world.print_body(session, сосед_id, здесь)
    with pytest.raises(transport.AlreadyHarnessed):
        await transport.harness(session, constants, catalog, сосед, телега)


async def test_распрячься_оставляет_обоз_с_грузом(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Бросить гружёный обоз — нормальный ход игры, а не ошибка движка."""
    _, _, body, телега = await _обоз(session)
    await transport.harness(session, constants, catalog, body, телега)
    груз = await _в_руки(session, body, 20)
    await transport.load(session, constants, catalog, body, груз)

    await transport.unharness(session, body)
    assert await transport.harnessed(session, body) is None
    assert await transport.cargo_mass(session, catalog, телега) == pytest.approx(20)


# --- трюм -------------------------------------------------------------------


async def test_груз_едет_в_трюме_а_не_в_руках(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Ради этого всё и сделано: предел рук обходят повозкой."""
    _, _, body, телега = await _обоз(session)
    await transport.harness(session, constants, catalog, body, телега)

    предел_рук = await gear.capacity(session, constants, catalog, body)
    #: Больше, чем можно унести, — и всё это уезжает в трюме.
    груз = await _в_руки(session, body, предел_рук * 3)
    перенесено = await transport.load(session, constants, catalog, body, груз)

    assert перенесено == pytest.approx(предел_рук * 3)
    assert await gear.load_of(session, catalog, body) == pytest.approx(0), (
        "погруженное больше не в руках"
    )
    assert await transport.cargo_mass(session, catalog, телега) > предел_рук


async def test_трюм_не_резиновый(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body, телега = await _обоз(session, транспорт=ТАЧКА)
    await transport.harness(session, constants, catalog, body, телега)
    предел = transport.capacity(constants, ТАЧКА)
    груз = await _в_руки(session, body, предел + 1)
    with pytest.raises(transport.Overloaded):
        await transport.load(session, constants, catalog, body, груз)


async def test_выгрузка_упирается_в_предел_рук(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Из трюма берут руками: предел носимого никуда не девается (D-146)."""
    _, _, body, телега = await _обоз(session)
    await transport.harness(session, constants, catalog, body, телега)
    груз = await _в_руки(session, body, 100)
    await transport.load(session, constants, catalog, body, груз)

    в_трюме = (await transport.cargo_items(session, телега))[0]
    with pytest.raises(gear.Overloaded):
        await transport.unload(session, constants, catalog, body, в_трюме)

    #: По горсти — можно, и это честная цена: рука мала, а повозка большая.
    сколько = await transport.unload(session, constants, catalog, body, в_трюме, 10)
    assert сколько == pytest.approx(10)
    assert await gear.load_of(session, catalog, body) == pytest.approx(10)


# --- дорога (D-107) ---------------------------------------------------------


async def test_бездорожье_обоз_не_пускает(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Дорога — предусловие торговли, а не удобство."""
    _, там, body, телега = await _обоз(session, surface=Surface.TRAIL)
    await transport.harness(session, constants, catalog, body, телега)
    with pytest.raises(transport.Impassable):
        await travel.depart(session, constants, body, там)

    #: Пеший той же тропой проходит: запрет на транспорте, а не на человеке.
    await transport.unharness(session, body)
    assert await travel.depart(session, constants, body, там) is not None


async def test_тяжёлому_нужен_тракт(constants: Constants) -> None:
    """Лёгкий идёт по дороге, тяжёлый — только по мощёному (D-107)."""
    лёгкий, тяжёлый = ТЕЛЕГА, "Орбитальный корабль"
    assert not transport.heavy(constants, лёгкий)
    assert transport.heavy(constants, тяжёлый)
    assert transport.passable(constants, Surface.ROAD, лёгкий)
    assert not transport.passable(constants, Surface.ROAD, тяжёлый)
    assert transport.passable(constants, Surface.PAVED, тяжёлый)
    assert not transport.passable(constants, Surface.TRAIL, лёгкий)


async def test_обоз_идёт_быстрее_и_не_тратит_сил(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Повозка везёт и время, и тело: `transport.speed_k` и `stamina_k`."""
    _, там, пеший, _ = await _обоз(session)
    пешком = await travel.depart(session, constants, пеший, там)
    пеших_секунд = (пешком.arrives_at - пешком.started_at).total_seconds()
    было = float(пеший.stamina)
    assert float(пеший.stamina) < constants[R.BODY_STAMINA_MAX]

    _, туда, возчик, телега = await _обоз(session)
    await transport.harness(session, constants, catalog, возчик, телега)
    силы_до = float(возчик.stamina)
    обозом = await travel.depart(session, constants, возчик, туда)
    обозных_секунд = (обозом.arrives_at - обозом.started_at).total_seconds()

    assert обозных_секунд == pytest.approx(
        пеших_секунд / transport.speed(constants, ТЕЛЕГА), rel=1e-3
    )
    assert float(возчик.stamina) == pytest.approx(силы_до), "везёт транспорт, а не ноги"
    assert было < constants[R.BODY_STAMINA_MAX]


async def test_маршрут_обоза_строится_по_проходимым(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Пеший срежет тропой, обоз пойдёт кругом по дороге."""
    метка = uuid.uuid4().hex[:8]
    a = await world.create_node(session, f"terra.ra.{метка}", "А", area_m2=100)
    b = await world.create_node(session, f"terra.rb.{метка}", "Б", area_m2=100)
    c = await world.create_node(session, f"terra.rc.{метка}", "В", area_m2=100)
    #: Прямая тропа коротка, но обозу закрыта; дорога через Б длиннее.
    await travel.connect(session, a, c, base_seconds=60, surface=Surface.TRAIL)
    await travel.connect(session, a, b, base_seconds=300, surface=Surface.ROAD)
    await travel.connect(session, b, c, base_seconds=300, surface=Surface.ROAD)

    пеший = await travel.route(session, constants, a.id, c.id)
    assert пеший == [c.id], "пешему тропа короче"

    обозом = await travel.route(session, constants, a.id, c.id, vehicle=ТЕЛЕГА)
    assert обозом == [b.id, c.id], "обоз идёт кругом по дороге"


async def test_обозу_некуда_если_только_тропа(
    session: AsyncSession, constants: Constants
) -> None:
    метка = uuid.uuid4().hex[:8]
    a = await world.create_node(session, f"terra.sa.{метка}", "А", area_m2=100)
    b = await world.create_node(session, f"terra.sb.{метка}", "Б", area_m2=100)
    await travel.connect(session, a, b, base_seconds=60, surface=Surface.TRAIL)
    with pytest.raises(travel.NoRoute):
        await travel.route(session, constants, a.id, b.id, vehicle=ТЕЛЕГА)


# --- обоз в дороге ----------------------------------------------------------


async def test_обоз_приезжает_вместе_с_грузом(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Транспорт и трюм переезжают за телом — заданием журнала, как и тело."""
    async with factory() as session, session.begin():
        _, там, body, телега = await _обоз(session)
        await transport.harness(session, constants, catalog, body, телега)
        груз = await _в_руки(session, body, 30)
        await transport.load(session, constants, catalog, body, груз)
        переход = await travel.depart(session, constants, body, там)
        срок, body_id, там_id, телега_id = (
            переход.arrives_at, body.id, там.id, телега.id,
        )

    assert await jobs.run_one(factory, now=срок) is not None

    async with factory() as session:
        body = await session.get(Body, body_id)
        телега = await session.get(Item, телега_id)
        там = await session.get(Node, там_id)
        двор = await world.node_container(session, там)
        assert body.node_id == там_id
        assert телега.container_id == двор.id, "повозка приехала за телом"
        assert await transport.cargo_mass(session, catalog, телега) == pytest.approx(30)
        assert await transport.harnessed(session, body) is not None


async def test_обоз_изнашивается_за_отрезок(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Полный трюм изнашивает сильнее пустого: возят не воздух (D-129)."""
    async def проехать(груза: float) -> float:
        async with factory() as session, session.begin():
            _, там, body, телега = await _обоз(session)
            await transport.harness(session, constants, catalog, body, телега)
            if груза:
                await transport.load(
                    session, constants, catalog, body,
                    await _в_руки(session, body, груза),
                )
            переход = await travel.depart(session, constants, body, там)
            срок, телега_id = переход.arrives_at, телега.id
        assert await jobs.run_one(factory, now=срок) is not None
        async with factory() as session:
            телега = await session.get(Item, телега_id)
            return float(телега.condition)

    порожняя = await проехать(0)
    гружёная = await проехать(transport.capacity(constants, ТЕЛЕГА))
    assert порожняя < constants[R.QUALITY_SCALE].max, "переход изнашивает"
    assert гружёная < порожняя, "полный трюм изнашивает сильнее пустого"


async def test_поломка_останавливает_обоз_и_роняет_груз(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Поломка — остановка, а не потеря груза (D-157)."""
    async with factory() as session, session.begin():
        метка = uuid.uuid4().hex[:8]
        a = await world.create_node(session, f"terra.wa.{метка}", "А", area_m2=100)
        b = await world.create_node(session, f"terra.wb.{метка}", "Б", area_m2=100)
        c = await world.create_node(session, f"terra.wc.{метка}", "В", area_m2=100)
        await travel.connect(session, a, b, base_seconds=300)
        await travel.connect(session, b, c, base_seconds=300)
        identity = await world.create_identity(session, f"Возчик-{метка}")
        body = await world.print_body(session, identity, a)
        двор = await world.node_container(session, a)
        телега = await world.grant_item(
            session, двор, ТЕЛЕГА, amount=1, origin="сценарий теста"
        )
        #: Повозка на последнем издыхании: следующий отрезок её добьёт.
        телега.condition = Decimal("0.5")
        await transport.harness(session, constants, catalog, body, телега)
        await transport.load(
            session, constants, catalog, body, await _в_руки(session, body, 25)
        )

        переход = await travel.depart(session, constants, body, c)
        assert переход.plan, "маршрут из двух отрезков"
        срок, body_id, b_id, телега_id = переход.arrives_at, body.id, b.id, телега.id

    assert await jobs.run_one(factory, now=срок) is not None

    async with factory() as session:
        body = await session.get(Body, body_id)
        assert body.node_id == b_id, "обоз встал там, где сломался"
        assert await session.get(Item, телега_id) is None, "разбитая повозка кончилась"
        assert await transport.harnessed(session, body) is None, "упряжка распалась"
        assert await travel.current(session, body) is None, "маршрут прерван"

        двор = await world.node_container(session, await session.get(Node, b_id))
        лежит = (
            await session.execute(select(Item).where(Item.container_id == двор.id))
        ).scalars().all()
        assert [вещь.type_key for вещь in лежит] == [ГРУЗ], "груз остался в узле"


async def test_смерть_распрягает(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Мёртвый ничего не тянет, а обоз остаётся стоять с грузом."""
    _, _, body, телега = await _обоз(session)
    await transport.harness(session, constants, catalog, body, телега)
    await transport.load(
        session, constants, catalog, body, await _в_руки(session, body, 10)
    )

    await death.die(session, constants, body, cause="сценарий теста")
    assert await transport.harnessed(session, body) is None
    assert (
        await session.execute(select(Harness).where(Harness.item_id == телега.id))
    ).scalar_one_or_none() is None
    assert await transport.cargo_mass(session, catalog, телега) == pytest.approx(10)
