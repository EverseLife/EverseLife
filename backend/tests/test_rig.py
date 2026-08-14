"""Буровая установка: капитал вместо труда (D-115).

Проверяется то, ради чего установка введена именно такой:

* она работает без игрока и **не спит** — в этом вся её сила;
* и проигрывает человеку во всём остальном: выход ниже, качество ограничено
  `rig.quality_cap`, жилу выедает вдвое быстрее;
* три обязательства держат её на людях: топливо, бункер и обслуживание.
  Любое нарушенное — и машина стоит.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import rig, world
from src.models.inventory import Item
from src.units import SCALE_MAX, amount_float


async def _забой(session: AsyncSession, *, угля: float = 100, богатство: float = 60):
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.pit.{метка}", "Забой", area_m2=200)
    vein = await world.create_vein(
        session, node, "Железная руда", richness=богатство, remaining=100_000
    )
    двор = await world.node_container(session, node)
    if угля > 0:
        await world.grant_item(
            session, двор, rig.FUEL, amount=угля, quality=55, origin="тест"
        )
    identity = await world.create_identity(session, f"Промышленник-{метка}")
    body = await world.print_body(session, identity, node)
    карман = await world.body_container(session, body)
    станок = await world.grant_item(
        session, карман, rig.RIG, quality=70, origin="тест"
    )
    установка = await rig.place(session, body, станок, vein)
    return node, vein, body, установка, станок


def _через(установка, часов: float) -> datetime:
    return установка.counted_at + timedelta(hours=часов)


# --- работает без игрока ----------------------------------------------------


async def test_добывает_временем_и_жжёт_уголь(
    session: AsyncSession, constants: Constants
) -> None:
    """Машина не спит: бункер наполняется, пока хозяин занят другим."""
    node, _, _, установка, станок = await _забой(session)
    было_угля = 100.0

    добыто = await rig.advance(session, constants, установка, now=_через(установка, 4))
    #: Выход задан вольтом и от состояния не зависит: изношенная машина копает
    #: не меньше, а хуже — это видно в качестве при вывозе.
    assert добыто == pytest.approx(constants[R.RIG_OUTPUT_PER_HOUR] * 4)
    assert float(установка.hopper) == pytest.approx(добыто)

    двор = await world.node_container(session, node)
    осталось = await rig._coal_available(session, двор.id)  # noqa: SLF001
    assert осталось == pytest.approx(
        было_угля - constants[R.RIG_FUEL_PER_HOUR] * 4, rel=0.01
    )


async def test_машина_проигрывает_человеку_в_выходе(
    session: AsyncSession, constants: Constants
) -> None:
    """Ремесло — способ получить хорошую руду, буровая — много средней."""
    assert constants[R.RIG_OUTPUT_PER_HOUR] < constants[R.MINING_IRON_PER_HOUR]


# --- три обязательства ------------------------------------------------------


async def test_без_угля_установка_стоит(
    session: AsyncSession, constants: Constants
) -> None:
    """Кончилось топливо — встала. Отсюда постоянный контракт с углевозом."""
    _, _, _, установка, _ = await _забой(session, угля=0)
    добыто = await rig.advance(session, constants, установка, now=_через(установка, 5))
    assert добыто == 0
    assert float(установка.hopper) == 0


async def test_угля_хватает_ровно_на_свои_часы(
    session: AsyncSession, constants: Constants
) -> None:
    """Полтора часа топлива — полтора часа работы, а не пять."""
    часов = 1.5
    угля = constants[R.RIG_FUEL_PER_HOUR] * часов
    _, _, _, установка, _ = await _забой(session, угля=угля)

    добыто = await rig.advance(session, constants, установка, now=_через(установка, 5))
    assert добыто == pytest.approx(constants[R.RIG_OUTPUT_PER_HOUR] * часов, rel=0.01)


async def test_полный_бункер_останавливает_машину(
    session: AsyncSession, constants: Constants
) -> None:
    """Приезжать обязательно: без возчика предприятие не работает."""
    _, _, _, установка, _ = await _забой(session, угля=100_000)
    ёмкость = rig.hopper_capacity(constants)

    #: Заведомо больше, чем вмещает бункер.
    часов = constants[R.RIG_HOPPER_CAPACITY] * 3
    await rig.advance(session, constants, установка, now=_через(установка, часов))
    assert float(установка.hopper) == pytest.approx(ёмкость, rel=0.02)

    #: И дальше не растёт, сколько ни жди.
    ещё = await rig.advance(
        session, constants, установка, now=_через(установка, часов)
    )
    assert ещё == 0


async def test_установка_изнашивается_и_заброшенная_разваливается(
    session: AsyncSession, constants: Constants
) -> None:
    """`rig.wear_per_day` идёт по времени, а не по добытому.

    Хорошая машина изнашивается медленнее — тем же общим правилом, что кирка и
    наковальня (D-129): второй формулы для буровой не заводится.
    """
    from src.engine import wear

    _, _, _, установка, станок = await _забой(session)
    было = float(станок.condition)
    сутки = constants[R.TIME_DAY_TERRA]
    await rig.advance(session, constants, установка, now=_через(установка, сутки))

    срок = wear.life_factor(constants, float(станок.quality))
    assert float(станок.condition) == pytest.approx(
        было - constants[R.RIG_WEAR_PER_DAY] / срок, abs=0.01
    )


# --- вывоз и качество -------------------------------------------------------


async def test_вывоз_бункера_ногами_и_качество_под_потолком(
    session: AsyncSession, constants: Constants
) -> None:
    """Машина работает по настройке: выше `rig.quality_cap` она не даёт."""
    _, vein, body, установка, _ = await _забой(session, богатство=80)
    #: Час работы, а не три: бункер вывозят руками, и руки не бездонны
    #: (D-146). Полный бункер — работа для возчика, а не для карманов.
    await rig.advance(session, constants, установка, now=_через(установка, 1))

    взято = await rig.empty_hopper(session, constants, body, установка)
    assert взято > 0
    assert float(установка.hopper) == 0

    карман = await world.body_container(session, body)
    from sqlalchemy import select

    руда = (
        await session.execute(
            select(Item).where(
                Item.container_id == карман.id, Item.type_key == vein.resource
            )
        )
    ).scalars().all()
    assert руда, "бункер переехал в карман"
    качество = float(руда[0].quality)
    assert качество == pytest.approx(constants[R.RIG_QUALITY_CAP])
    assert качество < 80, "богатая жила машине не помогает — она ровна по настройке"
    assert amount_float(руда[0].amount) == pytest.approx(взято, rel=0.01)


async def test_разбитая_машина_даёт_худшую_руду(
    session: AsyncSession, constants: Constants
) -> None:
    """Содержание обязательно: изношенная не ломается внезапно, а работает хуже."""
    _, _, body, установка, станок = await _забой(session, богатство=80)
    from decimal import Decimal

    станок.condition = Decimal("20")
    await session.flush()
    await rig.advance(session, constants, установка, now=_через(установка, 1))
    await rig.empty_hopper(session, constants, body, установка)

    from sqlalchemy import select

    карман = await world.body_container(session, body)
    руда = (
        await session.execute(
            select(Item).where(Item.container_id == карман.id, Item.type_key == "Железная руда")
        )
    ).scalars().all()
    качество = float(руда[0].quality)
    assert качество < constants[R.RIG_QUALITY_CAP], "потолок опустился с износом"


async def test_чужой_бункер_не_вывозят(
    session: AsyncSession, constants: Constants
) -> None:
    """Вывоз — по договору с хозяином, а не по факту прихода (D-116)."""
    node, _, _, установка, _ = await _забой(session)
    чужой_id = await world.create_identity(session, f"Чужой-{uuid.uuid4().hex[:6]}")
    чужое_тело = await world.print_body(session, чужой_id, node)

    with pytest.raises(rig.NotYours):
        await rig.empty_hopper(session, constants, чужое_тело, установка)


async def test_жилу_выедает_вдвое_быстрее(
    session: AsyncSession, constants: Constants
) -> None:
    """Капитал ускоряет истощение мира — и это повод для спора у жилы (D-101)."""
    _, vein, _, установка, _ = await _забой(session)
    было = vein.remaining

    добыто = await rig.advance(session, constants, установка, now=_через(установка, 4))
    ушло = amount_float(было - vein.remaining)
    assert ушло == pytest.approx(
        добыто * constants[R.RIG_DEPLETION_MULTIPLIER], rel=0.01
    )
