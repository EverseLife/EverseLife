"""Агротехника как знание (D-057).

Ради чего разделены семена и знание: **посадить можно что угодно, а вырастить
хорошо — только зная, чего растению надо**. Никаких запретов; разница в том,
что видит фермер.

* без агротехники сводка даёт симптомы и ни одного числа нормы;
* с агротехникой — нормы и остаток до них;
* агротехника восьми базовых лежит в Библиотеке и берётся ногами;
* агротехнику выведенного сорта знает только его автор.
"""

from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import breed, farm, world
from src.models.farm import PlotState
from src.models.identity import KnowledgeKind
from src.units import PERCENT

SPELT = "spelt"


async def _поле(session: AsyncSession, *, библиотека: bool = False, питомник: bool = False):
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session, f"terra.agro.{метка}", "Поле", area_m2=400,
        properties={"вода": "река", "плодородие": 30, "library": библиотека},
    )
    if питомник:
        двор = await world.node_container(session, node)
        await world.grant_item(session, двор, breed.NURSERY, quality=60, origin="тест")
    identity = await world.create_identity(session, f"Новичок-{метка}")
    body = await world.print_body(session, identity, node)
    node.owner_identity_id = identity.id
    await session.flush()
    return node, identity, body


async def _засеяно(session, constants, catalog, body, *, плодородие=30.0):
    """Делянка с растущей полбой на скудной земле — чтобы было чему болеть."""
    сорт = await breed.landrace(session, catalog, SPELT)
    карман = await world.body_container(session, body)
    семена = await breed.seed_lot(session, catalog, карман.id, сорт, 500, PERCENT)
    plot = await farm.mark(session, constants, body, name="грядка", area=10)
    plot.state = PlotState.PLOWED
    from decimal import Decimal

    plot.fertility = Decimal(str(плодородие))
    await session.flush()
    await farm.sow(session, constants, catalog, body, plot, семена)
    return plot, сорт


# --- что видно ---------------------------------------------------------------


async def test_без_агротехники_видны_симптомы_а_не_нормы(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Догадывайся: перелив или засуха? Знание это и превращает в задачу."""
    _, identity, body = await _засеяно_поле(session, constants, catalog)

    (строка,) = await farm.survey(session, constants, catalog, identity.id)
    assert строка["agrotech"] is False
    assert "symptoms" in строка and строка["symptoms"], "симптом обязан быть виден"
    for число in ("ripe_at", "missed_days", "water_need", "fertility_required"):
        assert число not in строка, f"норма {число} без агротехники не показывается"

    #: Земля скудная — лист бледный; сутки не обходили — лист вялый.
    assert "pale" in строка["symptoms"]
    assert "thirst" in строка["symptoms"]


async def test_с_агротехникой_видны_нормы_и_остаток(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, identity, body = await _засеяно_поле(session, constants, catalog)
    await world.learn(session, identity, SPELT, kind=KnowledgeKind.AGROTECH)

    (строка,) = await farm.survey(session, constants, catalog, identity.id)
    assert строка["agrotech"] is True
    assert "symptoms" not in строка
    plant = catalog.plants.by_id(SPELT)
    assert строка["fertility_required"] == pytest.approx(plant.requires.fertility)
    assert строка["water_need"] == pytest.approx(constants[R.FARM_WATER_PER_M2] * 10)
    assert строка["ripe_at"] and строка["missed_days"] >= 0


async def _засеяно_поле(session, constants, catalog):
    node, identity, body = await _поле(session)
    await _засеяно(session, constants, catalog, body)
    return node, identity, body


# --- откуда берётся знание ---------------------------------------------------


async def test_агротехника_базовых_лежит_в_библиотеке_и_берётся_ногами(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Бесплатно и без условий, но только придя (D-053)."""
    _, _, в_поле = await _поле(session)
    with pytest.raises(breed.BreedError):
        await breed.copy_agrotech(session, catalog, в_поле, SPELT)

    _, identity, в_библиотеке = await _поле(session, библиотека=True)
    знание = await breed.copy_agrotech(session, catalog, в_библиотеке, SPELT)
    assert знание is not None

    сорт = await breed.landrace(session, catalog, SPELT)
    assert await breed.knows_agrotech(session, identity.id, сорт)


async def test_агротехнику_выведенного_знает_только_автор(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Монополия селекционера: её нельзя взять в Библиотеке (D-057)."""
    _, автор_личность, автор = await _поле(session, питомник=True)
    _, чужой_личность, _ = await _поле(session)

    базовый = await breed.landrace(session, catalog, SPELT)
    from src.models.plant import Variety

    другой = Variety(
        culture_id=SPELT, name="Скороспелка", stable=True, generation=0,
        traits={**базовый.traits,
                "yield_per_m2": базовый.traits["yield_per_m2"] * 2,
                "cycle_days": базовый.traits["cycle_days"] / 2},
    )
    session.add(другой)
    await session.flush()

    карман = await world.body_container(session, автор)
    a = await breed.seed_lot(session, catalog, карман.id, базовый, 500, PERCENT)
    b = await breed.seed_lot(session, catalog, карман.id, другой, 500, PERCENT)
    питомник = await breed.cross(session, constants, catalog, автор, a, b)
    гибрид = await breed.gather_cross(
        session, constants, catalog, автор, питомник,
        now=питомник.ready_at, rng=random.Random(7),
    )
    assert гибрид is not None

    assert await breed.knows_agrotech(session, автор_личность.id, гибрид)
    assert not await breed.knows_agrotech(session, чужой_личность.id, гибрид)
    #: И в Библиотеке её нет: туда попадают только базовые восемь. Здесь автор
    #: вдобавок стоит не в Библиотеке, так что отказ приходит раньше — на
    #: присутствии (D-053).
    with pytest.raises(breed.BreedError):
        await breed.copy_agrotech(session, catalog, автор, str(гибрид.id))
