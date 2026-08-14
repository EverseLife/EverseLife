"""Станок занимает один работник, и ставят его у себя — в здание (D-106, D-150).

Проверяется то, ради чего правило заведено:

* пока идёт партия, станок занят и второму не отдаётся;
* кончилась партия — свободен;
* станок ставится в **свой** узел, а не в любой; занятый работой не уносится;
* без здания станок не встаёт, а в тесном здании кончаются места.

Следствие, ради которого всё это и сделано: городская мастерская перестаёт
быть бесплатным цехом на весь город, и ремесленнику становится нужен свой
станок у себя дома.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import craft, estate, station, world
from src.models.estate import Building
from src.models.inventory import Item

BENCH = "Верстак"
MAKE = "Брус"


async def _мастерская(session: AsyncSession, *, сколько_станков: int = 1):
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session, f"terra.shop.{метка}", "Мастерская", area_m2=200
    )
    #: Станок живёт в здании (D-106): мастерская теста застроена целиком.
    session.add(Building(node_id=node.id, area_m2=200))
    await session.flush()
    двор = await world.node_container(session, node)
    for _ in range(сколько_станков):
        await world.grant_item(session, двор, BENCH, quality=60, origin="тест")
    return node


async def _мастер(session: AsyncSession, node, имя: str):
    identity = await world.create_identity(session, f"{имя}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    await world.learn(session, identity, MAKE)
    карман = await world.body_container(session, body)
    await world.grant_item(
        session, карман, "Дерево", amount=50, quality=60, origin="тест"
    )
    return identity, body


async def test_станок_занят_одним(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Сто игроков у одной наковальни — не мастерская, а очередь, которой нет."""
    node = await _мастерская(session)
    _, первый = await _мастер(session, node, "Первый")
    _, второй = await _мастер(session, node, "Второй")

    await craft.start(session, constants, catalog, первый, MAKE, 1)
    with pytest.raises(craft.Busy):
        await craft.start(session, constants, catalog, второй, MAKE, 1)


async def test_один_станок_одна_работа(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Занят — значит занят, в том числе для самого мастера.

    Иначе хозяин станка запускал бы у него сколько угодно партий разом, и
    правило «за станком работает один» держалось бы только против чужих.
    """
    node = await _мастерская(session)
    _, мастер = await _мастер(session, node, "Мастер")
    await craft.start(session, constants, catalog, мастер, MAKE, 1)
    with pytest.raises(craft.Busy):
        await craft.start(session, constants, catalog, мастер, MAKE, 1)


async def test_второй_станок_снимает_очередь(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Мест в мастерской ровно столько, сколько станков."""
    node = await _мастерская(session, сколько_станков=2)
    _, первый = await _мастер(session, node, "Первый")
    _, второй = await _мастер(session, node, "Второй")

    один = await craft.start(session, constants, catalog, первый, MAKE, 1)
    другой = await craft.start(session, constants, catalog, второй, MAKE, 1)
    assert один.station_item_id != другой.station_item_id


async def test_станок_освобождается_с_концом_партии(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node = await _мастерская(session)
    _, мастер = await _мастер(session, node, "Мастер")
    партия = await craft.start(session, constants, catalog, мастер, MAKE, 1)

    станок = await session.get(Item, партия.station_item_id)
    assert станок.busy_body_id == мастер.id

    from sqlalchemy import select

    from src.models.job import Job, JobKind, JobState

    задание = (
        await session.execute(
            select(Job).where(
                Job.kind == JobKind.CRAFT_BATCH.value, Job.state == JobState.PENDING
            )
        )
    ).scalars().first()
    await craft.finish(session, задание)
    await session.refresh(станок)
    assert станок.busy_body_id is None


async def test_станок_ставят_у_себя(
    session: AsyncSession, catalog: Catalog
) -> None:
    """Чужой узел не застроишь: в этом и смысл своего дома."""
    node = await _мастерская(session, сколько_станков=0)
    identity, body = await _мастер(session, node, "Хозяин")
    карман = await world.body_container(session, body)
    станок = await world.grant_item(
        session, карман, BENCH, quality=60, origin="тест"
    )

    with pytest.raises(station.NotYours):
        await station.place(session, catalog, body, станок)

    await world.claim_node(session, body, node)
    await station.place(session, catalog, body, станок)
    двор = await world.node_container(session, node)
    assert станок.container_id == двор.id


async def test_без_здания_станок_не_встаёт(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Сначала строят, потом обставляют (D-106): двор — не мастерская."""
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session, f"terra.bare.{метка}", "Пустырь", area_m2=200
    )
    identity, body = await _мастер(session, node, "Хозяин")
    await world.claim_node(session, body, node)
    карман = await world.body_container(session, body)
    станок = await world.grant_item(session, карман, BENCH, quality=60, origin="тест")

    with pytest.raises(estate.NoBuilding):
        await station.place(session, catalog, body, станок)


async def test_станки_занимают_площадь_здания(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Место — `build.slots_per_area` м² на вещь: в тесном доме станки не множатся."""
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session, f"terra.tiny.{метка}", "Тесный дом", area_m2=100
    )
    #: Здание на одно место: второе не влезает.
    session.add(Building(node_id=node.id, area_m2=10))
    await session.flush()
    identity, body = await _мастер(session, node, "Хозяин")
    await world.claim_node(session, body, node)
    карман = await world.body_container(session, body)

    первый = await world.grant_item(session, карман, BENCH, quality=60, origin="тест")
    await station.place(session, catalog, body, первый)

    второй = await world.grant_item(session, карман, BENCH, quality=60, origin="тест")
    with pytest.raises(estate.NoRoom):
        await station.place(session, catalog, body, второй)


async def test_мебель_ставится_как_станок_но_мебелью(
    session: AsyncSession, catalog: Catalog
) -> None:
    """Кровать — мебель, а не станок (D-090): ставится в здание тем же путём."""
    node = await _мастерская(session, сколько_станков=0)
    _, body = await _мастер(session, node, "Хозяин")
    await world.claim_node(session, body, node)
    карман = await world.body_container(session, body)
    кровать = await world.grant_item(session, карман, "Кровать", quality=60, origin="тест")

    assert station.is_furniture(catalog, "Кровать")
    assert not station.is_station(catalog, "Кровать")
    await station.place(session, catalog, body, кровать)
    двор = await world.node_container(session, node)
    assert кровать.container_id == двор.id


async def test_не_станок_в_узел_не_ставится(
    session: AsyncSession, catalog: Catalog
) -> None:
    node = await _мастерская(session, сколько_станков=0)
    _, body = await _мастер(session, node, "Хозяин")
    await world.claim_node(session, body, node)
    карман = await world.body_container(session, body)
    мешок = (
        await world.grant_item(
            session, карман, "Дерево", amount=1, quality=60, origin="тест"
        )
    )
    with pytest.raises(station.NotStation):
        await station.place(session, catalog, body, мешок)


async def test_занятый_станок_не_уносят(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Унести станок из-под работающего нельзя — даже свой."""
    node = await _мастерская(session)
    _, мастер = await _мастер(session, node, "Мастер")
    await world.claim_node(session, мастер, node)
    партия = await craft.start(session, constants, catalog, мастер, MAKE, 1)

    станок = await session.get(Item, партия.station_item_id)
    with pytest.raises(station.Busy):
        await station.take(session, catalog, мастер, станок)
