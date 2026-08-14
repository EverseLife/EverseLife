"""Чат локации (D-043, D-050).

Проверяется то, ради чего он устроен именно так:

* разговор в комнате: слышат находящиеся в локации, из другой — тишина;
* вышел — вышел из разговора: после возвращения продолжения не слышно;
* тип обязателен, и их три: речь, действие, вне игры;
* кружки видны, их содержание — нет; долетевшее помечено как обрывок;
* вполголоса — меньше утечек; формула собрана из констант вольта;
* истории нет: буфер доставки подметается, а не хранится.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import chat, jobs, travel, world
from src.models.chat import ChatMessage, Utterance
from src.models.identity import Body
from src.models.travel import TravelState


async def _комната(session: AsyncSession, *, сколько_людей: int = 2):
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.room.{метка}", "Комната", area_m2=100)
    тела = []
    for i in range(сколько_людей):
        identity = await world.create_identity(session, f"Гость-{метка}-{i}")
        тела.append(await world.print_body(session, identity, node))
    return node, тела


async def _слышит(session: AsyncSession, body: Body) -> list[str]:
    return [line.text for line in await chat.hear(session, body)]


# --- комната ----------------------------------------------------------------


async def test_слышат_находящиеся_в_локации(
    session: AsyncSession, constants: Constants
) -> None:
    _, (один, другой) = await _комната(session)
    await chat.say(session, constants, один, "почём нынче сталь?", kind=Utterance.SPEECH)
    assert await _слышит(session, другой) == ["почём нынче сталь?"]


async def test_из_другой_локации_тишина(
    session: AsyncSession, constants: Constants
) -> None:
    """Это разговор в комнате, а не канал."""
    _, (говорящий,) = await _комната(session, сколько_людей=1)
    _, (далёкий,) = await _комната(session, сколько_людей=1)
    await chat.say(session, constants, говорящий, "тайна", kind=Utterance.SPEECH)
    assert await _слышит(session, далёкий) == []


async def test_вышел__вышел_из_разговора(
    session: AsyncSession, constants: Constants
) -> None:
    """Вернувшись, продолжения не услышишь: слышно только с момента прихода."""
    node, (один, другой) = await _комната(session)
    прочь = await world.create_node(session, f"terra.away.{uuid.uuid4().hex[:6]}", "Прочь",
                                    area_m2=50)
    await travel.connect(session, node, прочь, base_seconds=5)

    await chat.say(session, constants, один, "пока ты здесь", kind=Utterance.SPEECH)
    поход = await travel.depart(session, constants, другой, прочь)

    #: В пути не слышно ничего: тебя нет в комнате.
    with pytest.raises(travel.InTransit):
        await chat.hear(session, другой)

    #: Сказанное, пока его не было, потеряно для него навсегда.
    await chat.say(session, constants, один, "а это без тебя", kind=Utterance.SPEECH)

    #: Дошёл туда и вернулся — как пришёл бы воркер, только руками теста.
    #: Часы берём у базы: `at` сообщений ставит она же, и мешать их с часами
    #: машины теста нельзя.
    момент = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    поход.state = TravelState.ARRIVED
    поход.arrived_at = момент
    другой.node_id = прочь.id
    другой.node_since = момент
    await session.flush()
    обратно = await travel.depart(session, constants, другой, node)
    обратно.state = TravelState.ARRIVED
    обратно.arrived_at = момент
    другой.node_id = node.id
    другой.node_since = момент
    await session.flush()

    await chat.say(session, constants, один, "с возвращением", kind=Utterance.SPEECH)
    assert await _слышит(session, другой) == ["с возвращением"]


async def test_горизонт_это_поле_тела_а_не_история_переходов(
    session: AsyncSession, constants: Constants
) -> None:
    """Перенесённый правкой мира — без единой записи о переходе — тоже не
    слышит сказанного до себя: горизонт обязан быть у любого тела (D-043)."""
    node, (местный,) = await _комната(session, сколько_людей=1)
    _, (пришелец,) = await _комната(session, сколько_людей=1)
    await chat.say(session, constants, местный, "до пришельца", kind=Utterance.SPEECH)

    #: Правка мира: тело переставляется вместе с горизонтом, перехода нет.
    момент = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    пришелец.node_id = node.id
    пришелец.node_since = момент
    await session.flush()

    assert await _слышит(session, пришелец) == []
    await chat.say(session, constants, местный, "при пришельце", kind=Utterance.SPEECH)
    assert await _слышит(session, пришелец) == ["при пришельце"]


async def test_тип_обязателен_и_их_три(session: AsyncSession, constants: Constants) -> None:
    """Без «действия» отыгрыш неотличим от реплик, без «вне игры» метагейм
    протекает в игровое (D-050)."""
    _, (кто, слушатель) = await _комната(session)
    await chat.say(session, constants, кто, "куёт не глядя", kind=Utterance.ACTION)
    await chat.say(session, constants, кто, "я после работы", kind=Utterance.OOC)
    виды = {line.kind for line in await chat.hear(session, слушатель)}
    assert виды == {Utterance.ACTION, Utterance.OOC}


async def test_пустое_и_бесконечное_не_говорится(
    session: AsyncSession, constants: Constants
) -> None:
    _, (кто,) = await _комната(session, сколько_людей=1)
    with pytest.raises(chat.ChatError):
        await chat.say(session, constants, кто, "   ", kind=Utterance.SPEECH)
    from src.runtime import CHAT_TEXT_LIMIT

    with pytest.raises(chat.ChatError):
        await chat.say(session, constants, кто, "а" * (CHAT_TEXT_LIMIT + 1),
                       kind=Utterance.SPEECH)


# --- кружки -----------------------------------------------------------------


async def test_кружок_виден_а_содержание_нет(
    session: AsyncSession, constants: Constants
) -> None:
    """«Эти о чём-то договариваются» — сильный социальный сигнал (D-043)."""
    _, (заговорщик, второй, посторонний) = await _комната(session, сколько_людей=3)
    кружок = await chat.gather(session, заговорщик, name="о ценах")
    await chat.join(session, второй, кружок.id)

    #: Зерно подобрано: с ним бросок не даёт утечки.
    тихо = random.Random(3)
    await chat.say(session, constants, заговорщик, "скупаем сталь", kind=Utterance.SPEECH,
                   rng=тихо)

    #: Постороннему видно кружок и его состав, но не сказанное.
    assert await _слышит(session, посторонний) == []
    видно = await chat.circles(session, посторонний)
    assert len(видно) == 1
    assert видно[0].name == "о ценах"
    assert len(видно[0].members) == 2
    assert not видно[0].mine

    #: Участнику — слышно.
    assert await _слышит(session, второй) == ["скупаем сталь"]


async def test_утечка_помечена_обрывком(
    session: AsyncSession, constants: Constants
) -> None:
    """Долетевшее — одна фраза без контекста, с указанием кружка-источника."""
    _, (шепчущий, посторонний) = await _комната(session)
    await chat.gather(session, шепчущий, name="сговор")

    #: Всегда «долетело»: нижняя граница броска.
    громко = random.Random()
    громко.uniform = lambda a, b: a  # noqa: ARG005
    await chat.say(session, constants, шепчущий, "делим жилу в полночь",
                   kind=Utterance.SPEECH, rng=громко)

    подслушано = await chat.hear(session, посторонний)
    assert len(подслушано) == 1
    assert подслушано[0].overheard
    assert подслушано[0].source == "сговор"


async def test_вполголоса_меньше_утечек(session: AsyncSession, constants: Constants) -> None:
    """Единственный рычаг говорящего — режим речи, не характеристика (D-058)."""
    node, тела = await _комната(session, сколько_людей=6)
    кружок = await chat.gather(session, тела[0], name=None)
    for тело in тела[1:4]:
        await chat.join(session, тело, кружок.id)

    шанс = await chat.leak_chance(constants, session, node, group_size=4)
    ожидание = (
        constants[R.CHAT_LEAK_BASE]
        + constants[R.CHAT_LEAK_PER_PERSON] * (6 - constants[R.CHAT_LEAK_CROWD_FREE])
        + constants[R.CHAT_LEAK_GROUP_SIZE] * (4 - constants[R.CHAT_LEAK_GROUP_FREE])
    )
    assert шанс == pytest.approx(ожидание)
    assert constants[R.CHAT_LEAK_QUIET_MULTIPLIER] < 1, "вполголоса обязан помогать"


async def test_библиотека_выдаёт(session: AsyncSession, constants: Constants) -> None:
    """Тихая библиотека выдаёт, шумная кузница глушит — свойство места."""
    node, _ = await _комната(session)
    обычный = await chat.leak_chance(constants, session, node, group_size=1)
    node.properties = {"library": True}
    await session.flush()
    в_библиотеке = await chat.leak_chance(constants, session, node, group_size=1)
    assert в_библиотеке > обычный


async def test_уход_распускает_кружок(session: AsyncSession, constants: Constants) -> None:
    """Кружок не ходит следом: вышел ногами — вышел из разговора."""
    node, (один, второй) = await _комната(session)
    прочь = await world.create_node(session, f"terra.out.{uuid.uuid4().hex[:6]}", "Прочь",
                                    area_m2=50)
    await travel.connect(session, node, прочь, base_seconds=5)
    кружок = await chat.gather(session, один)
    await chat.join(session, второй, кружок.id)

    await travel.depart(session, constants, один, прочь)
    осталось = await chat.circles(session, второй)
    assert len(осталось) == 1
    assert len(осталось[0].members) == 1, "ушедшего в кружке больше нет"


# --- буфер, а не история ----------------------------------------------------


async def test_истории_нет__буфер_подметается(
    session: AsyncSession, constants: Constants
) -> None:
    """Сервер не ведёт истории разговоров: поднимать нечего (D-070)."""
    from src.runtime import CHAT_BUFFER

    _, (кто,) = await _комната(session, сколько_людей=1)
    await chat.say(session, constants, кто, "это забудется", kind=Utterance.SPEECH)

    подметено = await chat.prune(session, now=datetime.now(UTC) + CHAT_BUFFER * 2)
    await session.commit()
    assert подметено == 1
    осталось = await session.scalar(select(func.count()).select_from(ChatMessage))
    assert осталось == 0


async def test_тик_мира_подметает_буфер(factory, constants: Constants) -> None:
    """Подметание — обязанность мира, а не доброй воли клиента."""
    from src.engine import tick
    from src.runtime import CHAT_BUFFER

    async with factory() as session, session.begin():
        _, (кто,) = await _комната(session, сколько_людей=1)
        реплика = await chat.say(session, constants, кто, "мимолётное",
                                 kind=Utterance.SPEECH)
        #: Тик подметает по своим часам, а не по часам теста: старим реплику,
        #: а не переносим мир в будущее.
        реплика.at = datetime.now(UTC) - CHAT_BUFFER * 2
        await tick.ensure_scheduled(session)

    await jobs.run_due(factory, limit=2)

    async with factory() as session:
        осталось = await session.scalar(select(func.count()).select_from(ChatMessage))
        assert осталось == 0
