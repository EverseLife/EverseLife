"""Гибернация (D-091).

Проверяется то, ради чего сон устроен именно так:

* восстановление считается по фактически проспанному времени — тик не нужен;
* дома (с кроватью) быстрее ровно в `body.hibernation_home_k` раз;
* спать впрок нельзя: потолок — `body.stamina_max`, полному ложиться незачем;
* спящий недоступен для присутственного — этим сон и платит.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import chat, mining, rest, travel, world
from src.models.chat import Utterance


async def _усталый(session: AsyncSession, *, stamina: float = 40, кровать: bool = False):
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.camp.{метка}", "Привал", area_m2=100)
    identity = await world.create_identity(session, f"Усталый-{метка}")
    body = await world.print_body(session, identity, node)
    body.stamina = Decimal(str(stamina))
    if кровать:
        двор = await world.node_container(session, node)
        await world.grant_item(session, двор, rest.BED, quality=50, origin="тест")
    await session.flush()
    return node, body


async def test_сон_восстанавливает_по_времени(
    session: AsyncSession, constants: Constants
) -> None:
    """Начисление при пробуждении, по фактическим часам — офлайн и без тика."""
    _, body = await _усталый(session, stamina=40)
    лёг = datetime.now(UTC)
    await rest.sleep(session, constants, body, now=лёг)

    вернулось = await rest.wake(session, constants, body, now=лёг + timedelta(hours=2))
    await session.commit()

    assert вернулось == pytest.approx(2 * constants[R.BODY_HIBERNATION_RATE])
    assert float(body.stamina) == pytest.approx(40 + вернулось)
    assert body.sleeping_since is None


async def test_дома_быстрее(session: AsyncSession, constants: Constants) -> None:
    """Кровать и есть дом, пока своих построек нет (Э3)."""
    _, в_поле = await _усталый(session, stamina=10)
    _, дома = await _усталый(session, stamina=10, кровать=True)
    лёг = datetime.now(UTC)

    await rest.sleep(session, constants, в_поле, now=лёг)
    await rest.sleep(session, constants, дома, now=лёг)
    assert not в_поле.sleeping_home
    assert дома.sleeping_home

    час = лёг + timedelta(hours=1)
    просто = await rest.wake(session, constants, в_поле, now=час)
    с_кроватью = await rest.wake(session, constants, дома, now=час)
    assert с_кроватью == pytest.approx(просто * constants[R.BODY_HIBERNATION_HOME_K])


async def test_спать_впрок_нельзя(session: AsyncSession, constants: Constants) -> None:
    """Потолок — `body.stamina_max`; полному ложиться незачем."""
    _, почти_полный = await _усталый(session, stamina=constants[R.BODY_STAMINA_MAX] - 1)
    лёг = datetime.now(UTC)
    await rest.sleep(session, constants, почти_полный, now=лёг)
    await rest.wake(session, constants, почти_полный, now=лёг + timedelta(hours=50))
    assert float(почти_полный.stamina) == constants[R.BODY_STAMINA_MAX]

    with pytest.raises(rest.NotTired):
        await rest.sleep(session, constants, почти_полный)


async def test_спящий_недоступен_для_присутственного(
    session: AsyncSession, constants: Constants
) -> None:
    """Проспал — партию выкупили: этим гибернация и платит (D-091)."""
    node, body = await _усталый(session)
    жила = await world.create_vein(session, node, "Железная руда", richness=60, remaining=1000)
    соседний = await world.create_node(session, f"terra.next.{uuid.uuid4().hex[:6]}",
                                       "Рядом", area_m2=50)
    await travel.connect(session, node, соседний, base_seconds=10)

    await rest.sleep(session, constants, body)

    with pytest.raises(travel.Asleep):
        await mining.start(session, constants, body, жила)
    with pytest.raises(travel.Asleep):
        await travel.depart(session, constants, body, соседний)
    with pytest.raises(travel.Asleep):
        await chat.say(session, constants, body, "сплю и говорю", kind=Utterance.SPEECH)
    with pytest.raises(travel.Asleep):
        #: Спящий не ложится второй раз — он уже лежит.
        await rest.sleep(session, constants, body)

    #: Проснуться — можно всегда, это и есть выход.
    await rest.wake(session, constants, body)
    await mining.start(session, constants, body, жила)


async def test_в_пути_не_ложатся(session: AsyncSession, constants: Constants) -> None:
    node, body = await _усталый(session)
    соседний = await world.create_node(session, f"terra.far.{uuid.uuid4().hex[:6]}",
                                       "Даль", area_m2=50)
    await travel.connect(session, node, соседний, base_seconds=600)
    await travel.depart(session, constants, body, соседний)
    with pytest.raises(travel.InTransit):
        await rest.sleep(session, constants, body)
