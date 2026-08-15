"""Хранилище: сундук и стеллаж (D-181).

Проверяется то, ради чего механика заведена:

* хранилищем вещь делает **поле вольта**, а не имя в коде;
* кладут и берут своим ходом и в своём узле; в чужой сундук не лезут;
* предел — масса, та же, что у рук и у трюма; предел рук при выемке остаётся;
* полное хранилище не уносят: иначе мебель стала бы обходом носимого (D-146).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import station, storage, world
from src.models.estate import Building

CHEST = "Сундук"
GOODS = "Брус"


async def _двор(session: AsyncSession):
    """Свой участок со зданием: сундук ставят в дом, а не под открытым небом."""
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session, f"terra.home.{метка}", "Дом", area_m2=200
    )
    session.add(Building(node_id=node.id, area_m2=200))
    await session.flush()
    identity = await world.create_identity(session, f"Хозяин-{метка}")
    body = await world.print_body(session, identity, node)
    await world.claim_node(session, body, node)
    return node, identity, body


async def _сундук(session: AsyncSession, node):
    двор = await world.node_container(session, node)
    return await world.grant_item(
        session, двор, CHEST, quality=60, origin="тест"
    )


async def _добро(session: AsyncSession, body, сколько: float = 10):
    карман = await world.body_container(session, body)
    return await world.grant_item(
        session, карман, GOODS, amount=сколько, quality=55, origin="тест"
    )


def test_хранилищем_делает_вольт(catalog: Catalog) -> None:
    """Ни одного имени в коде: движок читает `store` (D-090, D-181)."""
    assert storage.is_storage(catalog, CHEST)
    assert storage.capacity(catalog, CHEST) > 0
    #: Кровать — мебель без вместимости: в неё не кладут.
    assert not storage.is_storage(catalog, "Кровать")
    assert not storage.is_storage(catalog, "Верстак")


async def test_положенное_лежит_и_забирается(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, _, body = await _двор(session)
    сундук = await _сундук(session, node)
    вещь = await _добро(session, body, 10)

    положено = await storage.put(session, constants, catalog, body, сундук, вещь, 6)
    assert положено == pytest.approx(6)
    внутри = await storage.content(session, сундук)
    assert sum(float(в.amount) / 1000 for в in внутри) == pytest.approx(6)
    assert await storage.stored_mass(session, catalog, сундук) > 0

    #: И обратно: сундук — не могила, вещи из него достают.
    взято = await storage.take(
        session, constants, catalog, body, сундук, внутри[0], 2
    )
    assert взято == pytest.approx(2)


async def test_больше_вместимости_не_влезет(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Предел — масса: сундук не бездонный, как и руки (D-146)."""
    from src.engine import gear

    node, _, body = await _двор(session)
    сундук = await _сундук(session, node)
    предел = storage.capacity(catalog, CHEST)
    за_штуку = gear.mass_of(catalog, GOODS, 1)
    лишку = предел / за_штуку + 10
    вещь = await _добро(session, body, лишку)

    with pytest.raises(storage.Full):
        await storage.put(session, constants, catalog, body, сундук, вещь, лишку)


async def test_в_чужой_сундук_не_лезут(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Доступ идёт за правом на узел: вскрыть чужое — дело суда (D-166)."""
    node, _, хозяин = await _двор(session)
    сундук = await _сундук(session, node)
    своё = await _добро(session, хозяин, 4)
    await storage.put(session, constants, catalog, хозяин, сундук, своё, 4)

    метка = uuid.uuid4().hex[:6]
    гость = await world.create_identity(session, f"Гость-{метка}")
    тело_гостя = await world.print_body(session, гость, node)
    чужое = await world.grant_item(
        session,
        await world.body_container(session, тело_гостя),
        GOODS, amount=2, quality=55, origin="тест",
    )

    with pytest.raises(storage.NotYours):
        await storage.put(session, constants, catalog, тело_гостя, сундук, чужое, 2)
    лежит = (await storage.content(session, сундук))[0]
    with pytest.raises(storage.NotYours):
        await storage.take(session, constants, catalog, тело_гостя, сундук, лежит, 1)


async def test_полный_сундук_не_уносят(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Иначе «забрать мебель» стало бы способом унести тонну в кармане."""
    node, _, body = await _двор(session)
    сундук = await _сундук(session, node)
    вещь = await _добро(session, body, 5)
    await storage.put(session, constants, catalog, body, сундук, вещь, 5)

    with pytest.raises(station.NotEmpty):
        await station.take(session, catalog, body, сундук)

    #: Разобрал — уносится обычным порядком.
    лежит = (await storage.content(session, сундук))[0]
    await storage.take(session, constants, catalog, body, сундук, лежит)
    await station.take(session, catalog, body, сундук)
    карман = await world.body_container(session, body)
    assert сундук.container_id == карман.id


async def test_предел_рук_при_выемке_остаётся(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Сундук не обходит носимое: из него достают столько, сколько унесёшь."""
    from src.engine import gear

    node, _, body = await _двор(session)
    сундук = await _сундук(session, node)
    предел_рук = await gear.capacity(session, constants, catalog, body)
    за_штуку = gear.mass_of(catalog, GOODS, 1)
    много = предел_рук / за_штуку + 5

    вещь = await _добро(session, body, много)
    await storage.put(session, constants, catalog, body, сундук, вещь, много)
    лежит = (await storage.content(session, сундук))[0]

    with pytest.raises(gear.Overloaded):
        await storage.take(session, constants, catalog, body, сундук, лежит, много)
