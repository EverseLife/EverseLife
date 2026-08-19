"""Location chat (D-043, D-050).

Checked is what it is built this way for:

* a conversation in a room: those in the location hear, from another -- silence;
* left -- left the conversation: after returning the continuation is not heard;
* the kind is mandatory, and there are three: speech, action, out-of-game;
* circles are visible, their content is not; what leaked is marked as a fragment;
* in an undertone -- fewer leaks; the formula is assembled from vault constants;
* there is no history: the delivery buffer is swept, not stored.
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


async def _room(session: AsyncSession, *, people_count: int = 2):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.room.{stamp}", "Комната", area_m2=100)
    bodies = []
    for i in range(people_count):
        identity = await world.create_identity(session, f"Гость-{stamp}-{i}")
        bodies.append(await world.print_body(session, identity, node))
    return node, bodies


async def _hears(session: AsyncSession, body: Body) -> list[str]:
    return [line.text for line in await chat.hear(session, body)]


# --- room --------------------------------------------------------------------


async def test_heard_by_those_in_location(
    session: AsyncSession, constants: Constants
) -> None:
    _, (one, other) = await _room(session)
    await chat.say(session, constants, one, "почём нынче сталь?", kind=Utterance.SPEECH)
    assert await _hears(session, other) == ["почём нынче сталь?"]


async def test_silence_from_other_location(
    session: AsyncSession, constants: Constants
) -> None:
    """This is a conversation in a room, not a channel."""
    _, (speaker,) = await _room(session, people_count=1)
    _, (distant,) = await _room(session, people_count=1)
    await chat.say(session, constants, speaker, "тайна", kind=Utterance.SPEECH)
    assert await _hears(session, distant) == []


async def test_left_means_left_conversation(
    session: AsyncSession, constants: Constants
) -> None:
    """On return you will not hear the continuation: heard only since arrival."""
    node, (one, other) = await _room(session)
    away = await world.create_node(session, f"terra.away.{uuid.uuid4().hex[:6]}", "Прочь",
                                    area_m2=50)
    await travel.connect(session, node, away, base_seconds=5)

    await chat.say(session, constants, one, "пока ты здесь", kind=Utterance.SPEECH)
    trip = await travel.depart(session, constants, other, away)

    #: En route nothing is heard: you are not in the room.
    with pytest.raises(travel.InTransit):
        await chat.hear(session, other)

    #: What was said while they were away is lost to them forever.
    await chat.say(session, constants, one, "а это без тебя", kind=Utterance.SPEECH)

    #: Got there and came back -- as the worker would have, only by the test's
    #: hands. We take the clock from the database: it sets the messages' `at`
    #: too, and mixing them with the test machine's clock is not allowed.
    moment = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    trip.state = TravelState.ARRIVED
    trip.arrived_at = moment
    other.node_id = away.id
    other.node_since = moment
    await session.flush()
    back = await travel.depart(session, constants, other, node)
    back.state = TravelState.ARRIVED
    back.arrived_at = moment
    other.node_id = node.id
    other.node_since = moment
    await session.flush()

    await chat.say(session, constants, one, "с возвращением", kind=Utterance.SPEECH)
    assert await _hears(session, other) == ["с возвращением"]


async def test_horizon_is_body_field_not_transit_history(
    session: AsyncSession, constants: Constants
) -> None:
    """One moved by a world edit -- without a single transit record -- also does
    not hear what was said before them: every body must have a horizon (D-043)."""
    node, (local,) = await _room(session, people_count=1)
    _, (outsider,) = await _room(session, people_count=1)
    await chat.say(session, constants, local, "до пришельца", kind=Utterance.SPEECH)

    #: A world edit: the body is moved together with its horizon, there is no transit.
    moment = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    outsider.node_id = node.id
    outsider.node_since = moment
    await session.flush()

    assert await _hears(session, outsider) == []
    await chat.say(session, constants, local, "при пришельце", kind=Utterance.SPEECH)
    assert await _hears(session, outsider) == ["при пришельце"]


async def test_kind_required_and_there_are_three(
    session: AsyncSession, constants: Constants
) -> None:
    """Without "action" roleplay is indistinguishable from remarks, without
    "out-of-game" metagame leaks into the in-game (D-050)."""
    _, (who, listener) = await _room(session)
    await chat.say(session, constants, who, "куёт не глядя", kind=Utterance.ACTION)
    await chat.say(session, constants, who, "я после работы", kind=Utterance.OOC)
    kinds = {line.kind for line in await chat.hear(session, listener)}
    assert kinds == {Utterance.ACTION, Utterance.OOC}


async def test_empty_and_endless_not_spoken(
    session: AsyncSession, constants: Constants
) -> None:
    _, (who,) = await _room(session, people_count=1)
    with pytest.raises(chat.ChatError):
        await chat.say(session, constants, who, "   ", kind=Utterance.SPEECH)
    from src.runtime import CHAT_TEXT_LIMIT

    with pytest.raises(chat.ChatError):
        await chat.say(session, constants, who, "а" * (CHAT_TEXT_LIMIT + 1),
                       kind=Utterance.SPEECH)


# --- circles -----------------------------------------------------------------


async def test_circle_visible_but_content_not(
    session: AsyncSession, constants: Constants
) -> None:
    """"These ones are arranging something" is a strong social signal (D-043)."""
    _, (conspirator, second, stranger) = await _room(session, people_count=3)
    circle = await chat.gather(session, conspirator, name="о ценах")
    await chat.join(session, second, circle.id)

    #: The seed is picked: with it the roll gives no leak.
    quiet = random.Random(3)
    await chat.say(session, constants, conspirator, "скупаем сталь", kind=Utterance.SPEECH,
                   rng=quiet)

    #: An outsider sees the circle and its membership, but not what was said.
    assert await _hears(session, stranger) == []
    visible = await chat.circles(session, stranger)
    assert len(visible) == 1
    assert visible[0].name == "о ценах"
    assert len(visible[0].members) == 2
    assert not visible[0].mine

    #: A member hears.
    assert await _hears(session, second) == ["скупаем сталь"]


async def test_leak_marked_as_fragment(
    session: AsyncSession, constants: Constants
) -> None:
    """What leaked is one phrase without context, with the source circle named."""
    _, (whisperer, stranger) = await _room(session)
    await chat.gather(session, whisperer, name="сговор")

    #: Always "leaked": the luckiest roll there is. Chance keeps a memory now
    #: (D-213), so the roll is `random()` against a growing threshold -- zero
    #: is below any of them.
    loud = random.Random()
    loud.random = lambda: 0.0
    await chat.say(session, constants, whisperer, "делим жилу в полночь",
                   kind=Utterance.SPEECH, rng=loud)

    overheard = await chat.hear(session, stranger)
    assert len(overheard) == 1
    assert overheard[0].overheard
    assert overheard[0].source == "сговор"


async def test_undertone_leaks_less(session: AsyncSession, constants: Constants) -> None:
    """The speaker's only lever is the speech mode, not a stat (D-058)."""
    node, bodies = await _room(session, people_count=6)
    circle = await chat.gather(session, bodies[0], name=None)
    for body in bodies[1:4]:
        await chat.join(session, body, circle.id)

    chance = await chat.leak_chance(constants, session, node, group_size=4)
    expected = (
        constants[R.CHAT_LEAK_BASE]
        + constants[R.CHAT_LEAK_PER_PERSON] * (6 - constants[R.CHAT_LEAK_CROWD_FREE])
        + constants[R.CHAT_LEAK_GROUP_SIZE] * (4 - constants[R.CHAT_LEAK_GROUP_FREE])
    )
    assert chance == pytest.approx(expected)
    assert constants[R.CHAT_LEAK_QUIET_MULTIPLIER] < 1, "вполголоса обязан помогать"


async def test_library_serves(session: AsyncSession, constants: Constants) -> None:
    """A quiet library gives away, a noisy forge muffles -- a place property."""
    node, _ = await _room(session)
    ordinary_ = await chat.leak_chance(constants, session, node, group_size=1)
    node.properties = {"library": True}
    await session.flush()
    in_library = await chat.leak_chance(constants, session, node, group_size=1)
    assert in_library > ordinary_


async def test_leaving_disbands_circle(session: AsyncSession, constants: Constants) -> None:
    """The circle does not follow: walked out -- left the conversation."""
    node, (one, second) = await _room(session)
    away = await world.create_node(session, f"terra.out.{uuid.uuid4().hex[:6]}", "Прочь",
                                    area_m2=50)
    await travel.connect(session, node, away, base_seconds=5)
    circle = await chat.gather(session, one)
    await chat.join(session, second, circle.id)

    await travel.depart(session, constants, one, away)
    left = await chat.circles(session, second)
    assert len(left) == 1
    assert len(left[0].members) == 1, "ушедшего в кружке больше нет"


# --- a buffer, not history ---------------------------------------------------


async def test_no_history_buffer_swept(
    session: AsyncSession, constants: Constants
) -> None:
    """The server keeps no conversation history: nothing to bring up (D-070)."""
    from src.runtime import CHAT_BUFFER

    _, (who,) = await _room(session, people_count=1)
    await chat.say(session, constants, who, "это забудется", kind=Utterance.SPEECH)

    swept = await chat.prune(session, now=datetime.now(UTC) + CHAT_BUFFER * 2)
    await session.commit()
    assert swept == 1
    left = await session.scalar(select(func.count()).select_from(ChatMessage))
    assert left == 0


async def test_world_tick_sweeps_buffer(factory, constants: Constants) -> None:
    """Sweeping is the world's duty, not the client's goodwill."""
    from src.engine import tick
    from src.runtime import CHAT_BUFFER

    async with factory() as session, session.begin():
        _, (who,) = await _room(session, people_count=1)
        remark = await chat.say(session, constants, who, "мимолётное",
                                 kind=Utterance.SPEECH)
        #: The tick sweeps by its own clock, not the test's: we age the remark
        #: rather than moving the world into the future.

        remark.at = datetime.now(UTC) - CHAT_BUFFER * 2
        await tick.ensure_scheduled(session)

    await jobs.run_due(factory, limit=2)

    async with factory() as session:
        left = await session.scalar(select(func.count()).select_from(ChatMessage))
        assert left == 0
