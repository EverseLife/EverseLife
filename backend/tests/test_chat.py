# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Location chat (D-043, D-050).

Checked is what it is built this way for:

* a conversation in a room: those in the location hear, from another -- silence;
* left -- left the conversation: after returning the continuation is not heard;
* the kind is mandatory, and there are three: speech, action, out-of-game;
* circles are visible, their content is not; what leaked is marked as a fragment;
* in an undertone -- fewer leaks; the formula is assembled from vault constants;
* there is no history: the delivery buffer is swept, not stored;
* who stands in the room is who is in no transit: the list and the crowd (D-290).
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.account import _people_here
from src.constants import Constants
from src.constants import registry as R
from src.engine import chat, jobs, travel, world
from src.models.chat import ChatMessage, Utterance
from src.models.identity import Body
from src.models.travel import TravelState
from src.models.world import Node


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


async def test_heard_by_those_in_location(session: AsyncSession, constants: Constants) -> None:
    _, (one, other) = await _room(session)
    await chat.say(session, constants, one, "почём нынче сталь?", kind=Utterance.SPEECH)
    assert await _hears(session, other) == ["почём нынче сталь?"]


async def test_silence_from_other_location(session: AsyncSession, constants: Constants) -> None:
    """This is a conversation in a room, not a channel."""
    _, (speaker,) = await _room(session, people_count=1)
    _, (distant,) = await _room(session, people_count=1)
    await chat.say(session, constants, speaker, "тайна", kind=Utterance.SPEECH)
    assert await _hears(session, distant) == []


async def test_left_means_left_conversation(session: AsyncSession, constants: Constants) -> None:
    """On return you will not hear the continuation: heard only since arrival."""
    node, (one, other) = await _room(session)
    away = await world.create_node(
        session, f"terra.away.{uuid.uuid4().hex[:6]}", "Прочь", area_m2=50
    )
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


async def test_empty_and_endless_not_spoken(session: AsyncSession, constants: Constants) -> None:
    _, (who,) = await _room(session, people_count=1)
    with pytest.raises(chat.ChatError):
        await chat.say(session, constants, who, "   ", kind=Utterance.SPEECH)
    from src.runtime import CHAT_TEXT_LIMIT

    with pytest.raises(chat.ChatError):
        await chat.say(session, constants, who, "а" * (CHAT_TEXT_LIMIT + 1), kind=Utterance.SPEECH)


# --- circles -----------------------------------------------------------------


async def test_circle_visible_but_content_not(session: AsyncSession, constants: Constants) -> None:
    """ "These ones are arranging something" is a strong social signal (D-043)."""
    _, (conspirator, second, stranger) = await _room(session, people_count=3)
    circle = await chat.gather(session, conspirator, name="о ценах")
    await chat.join(session, second, circle.id)

    #: The seed is picked: with it the roll gives no leak.
    quiet = random.Random(3)
    await chat.say(
        session, constants, conspirator, "скупаем сталь", kind=Utterance.SPEECH, rng=quiet
    )

    #: An outsider sees the circle and its membership, but not what was said.
    assert await _hears(session, stranger) == []
    visible = await chat.circles(session, stranger)
    assert len(visible) == 1
    assert visible[0].name == "о ценах"
    assert len(visible[0].members) == 2
    assert not visible[0].mine

    #: A member hears.
    assert await _hears(session, second) == ["скупаем сталь"]


async def test_leak_marked_as_fragment(session: AsyncSession, constants: Constants) -> None:
    """What leaked is one phrase without context, with the source circle named."""
    _, (whisperer, stranger) = await _room(session)
    await chat.gather(session, whisperer, name="сговор")

    #: Always "leaked": the luckiest roll there is. Chance keeps a memory now
    #: (D-213), so the roll is `random()` against a growing threshold -- zero
    #: is below any of them.
    loud = random.Random()
    loud.random = lambda: 0.0
    await chat.say(
        session, constants, whisperer, "делим жилу в полночь", kind=Utterance.SPEECH, rng=loud
    )

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
    """A quiet library gives away -- the legacy node property of old worlds (D-176)."""
    node, _ = await _room(session)
    ordinary_ = await chat.leak_chance(constants, session, node, group_size=1)
    node.properties = {"library": True}
    await session.flush()
    in_library = await chat.leak_chance(constants, session, node, group_size=1)
    assert in_library > ordinary_


async def test_place_sound_is_the_class_of_what_stands(
    session: AsyncSession, constants: Constants
) -> None:
    """A standing forge muffles, a standing library gives away, and together
    the noise wins (D-291). The place is known by the class of what stands in
    it (D-215), not by its name: the table has no row named after the thing."""
    node, _ = await _room(session)
    ordinary_ = await chat.leak_chance(constants, session, node, group_size=1)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "library", quality=50, origin="тест")
    in_library = await chat.leak_chance(constants, session, node, group_size=1)
    assert in_library > ordinary_
    await world.grant_item(session, yard, "forge", quality=50, origin="тест")
    with_forge = await chat.leak_chance(constants, session, node, group_size=1)
    assert with_forge < ordinary_, "шум сильнее тишины"


async def test_place_sound_binds_to_the_class_not_the_name(
    session: AsyncSession, constants: Constants
) -> None:
    """The table row is the class, and a member whose key differs from the
    class key is still found by it (D-215, D-291). A Forerunner heat plant
    is `precursor_heat_plant` of class `heat_plant`: a lookup by the thing's
    own key would find nothing and leave the room at its usual leak."""
    loud = constants.with_overrides({"chat.leak_location_modifier": {"heat_plant": 0.5}})
    node, _ = await _room(session)
    ordinary_ = await chat.leak_chance(loud, session, node, group_size=1)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "precursor_heat_plant", origin="тест")
    by_class = await chat.leak_chance(loud, session, node, group_size=1)
    assert by_class < ordinary_, "класс найден, хотя ключа вещи в таблице нет"


async def test_leaving_disbands_circle(session: AsyncSession, constants: Constants) -> None:
    """The circle does not follow: walked out -- left the conversation."""
    node, (one, second) = await _room(session)
    away = await world.create_node(
        session, f"terra.out.{uuid.uuid4().hex[:6]}", "Прочь", area_m2=50
    )
    await travel.connect(session, node, away, base_seconds=5)
    circle = await chat.gather(session, one)
    await chat.join(session, second, circle.id)

    await travel.depart(session, constants, one, away)
    left = await chat.circles(session, second)
    assert len(left) == 1
    assert len(left[0].members) == 1, "ушедшего в кружке больше нет"


# --- who stands in the room (D-290) ------------------------------------------


async def _road_out(session: AsyncSession, node: Node) -> Node:
    """A way out of the room to set out along."""
    away = await world.create_node(
        session, f"terra.road.{uuid.uuid4().hex[:6]}", "Прочь", area_m2=50
    )
    await travel.connect(session, node, away, base_seconds=5)
    return away


async def _here(session: AsyncSession, body: Body) -> list[str]:
    """`people.here` as the socket answers it, asked by `body`: the bodies named."""
    answer = await _people_here({"identity_id": body.identity_id}, session, {})
    return [row["body"] for row in answer["people"]]


async def test_a_traveller_is_in_nobodys_list(session: AsyncSession, constants: Constants) -> None:
    """One who has set out is not named by the room they left (D-290 p. 1).

    The body keeps the node it left in `node_id` until the arrival job moves
    it, and the list read `node_id` alone: the talk head named the
    traveller, and the hand-over menu offered them as a receiver.
    Turned back, they stand in the room again, and the list says so.
    """
    node, (stays, leaves) = await _room(session)
    away = await _road_out(session, node)
    assert await _here(session, stays) == [str(leaves.id)]

    await travel.depart(session, constants, leaves, away)
    assert leaves.node_id == node.id, "до прихода тело держит узел, откуда ушло"
    assert await _here(session, stays) == [], "ушедший в путь не стоит в комнате"

    await travel.turn_back(session, leaves)
    assert await _here(session, stays) == [str(leaves.id)], "повернувший назад снова здесь"


async def test_the_road_is_not_a_room_to_ask_about(
    session: AsyncSession, constants: Constants
) -> None:
    """Asked from the road, the question is refused (D-290 p. 1): there is no
    room to name, and the one left behind is not it."""
    node, (stays, leaves) = await _room(session)
    away = await _road_out(session, node)
    await travel.depart(session, constants, leaves, away)
    with pytest.raises(travel.InTransit):
        await _here(session, leaves)
    await travel.turn_back(session, leaves)
    assert await _here(session, leaves) == [str(stays.id)]


async def test_a_sleeper_is_named_and_asks_nothing(session: AsyncSession) -> None:
    """Sleep is not the road: a sleeper lies in the room and the room names
    them. Asking is another matter -- the list is part of live talk (D-290),
    and a sleeper hears no talk (`chat.hear`), so they are refused the same
    way. Whether a sleeper can take a thing is the hand-over's to judge."""
    _, (awake, asleep) = await _room(session)
    asleep.sleeping_since = datetime.now(UTC)
    await session.flush()
    assert await _here(session, awake) == [str(asleep.id)]
    with pytest.raises(travel.Asleep):
        await _here(session, asleep)


async def test_a_traveller_does_not_crowd_the_leak(
    session: AsyncSession, constants: Constants
) -> None:
    """The leak is priced by the crowd in the room (D-043), and one who has set
    out is not in it: counted by `node_id` alone, the road raised the odds of
    a room it had left. A sleeper still counts -- they lie in the room."""
    #: Every body counts, one point each: the difference is the crowd itself.
    crowded = constants.with_overrides({"chat.leak_crowd_free": 0, "chat.leak_per_person": 1})
    node, (_, sleeper, leaves) = await _room(session, people_count=3)
    away = await _road_out(session, node)
    full = await chat.leak_chance(crowded, session, node, group_size=0)

    sleeper.sleeping_since = datetime.now(UTC)
    await session.flush()
    assert await chat.leak_chance(crowded, session, node, group_size=0) == pytest.approx(full)

    await travel.depart(session, constants, leaves, away)
    on_the_road = await chat.leak_chance(crowded, session, node, group_size=0)
    assert on_the_road == pytest.approx(full - 1), "ушедший в путь не шумит в комнате"

    await travel.turn_back(session, leaves)
    assert await chat.leak_chance(crowded, session, node, group_size=0) == pytest.approx(full)


# --- a buffer, not history ---------------------------------------------------


async def test_no_history_buffer_swept(session: AsyncSession, constants: Constants) -> None:
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
        remark = await chat.say(session, constants, who, "мимолётное", kind=Utterance.SPEECH)
        #: The tick sweeps by its own clock, not the test's: we age the remark
        #: rather than moving the world into the future.

        remark.at = datetime.now(UTC) - CHAT_BUFFER * 2
        await tick.ensure_scheduled(session)

    #: The two clock schedulings, then the steps they fan out (wave 4).
    await jobs.run_due(factory, limit=32)

    async with factory() as session:
        left = await session.scalar(select(func.count()).select_from(ChatMessage))
        assert left == 0


async def test_a_sleeper_is_not_told_the_line(
    session: AsyncSession, constants: Constants, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sleeper hears nothing, and the live line must agree with `hear`.

    `chat.hear` refuses a sleeper (D-091, D-211: the body is stopped), but the
    talk also goes out unasked, and that copy reached them all the same -- the
    room's line by the node, the circle's by the member's name. Asleep, one was
    the only listener in the world who both raised the price of the room
    (`_people_in`, OQ-180) and got the goods.

    The room's note names those who may not hear rather than leaving them out
    of it: the push has no session of its own to ask the world with.
    """
    notes: list[dict] = []

    async def kept(session, **note):
        notes.append(note)

    monkeypatch.setattr(chat.events, "announce", kept)
    node, (speaker, sleeper) = await _room(session)
    #: Joined while awake -- the circle is gathered in person (D-211).
    group = await chat.gather(session, speaker, name="Кружок")
    await chat.join(session, sleeper, group.id)
    await chat.leave_groups(session, speaker.identity_id)
    sleeper.sleeping_since = datetime.now(UTC)
    await session.flush()

    await chat.say(session, constants, speaker, "слышно?", kind=Utterance.SPEECH)
    room = [one for one in notes if one.get("event") == "chat.said"]
    assert len(room) == 1, "общий разговор уходит одной запиской на узел"
    assert room[0]["node_id"] == node.id
    assert room[0]["asleep"] == [str(sleeper.identity_id)], "спящий назван в записке"

    #: The circle is addressed by name, and a name is dropped outright.
    await chat.join(session, speaker, group.id)
    notes.clear()
    await chat.say(session, constants, speaker, "шёпот", kind=Utterance.SPEECH, quiet=True)
    told = {one.get("identity_id") for one in notes if one.get("event") == "chat.said"}
    assert speaker.identity_id in told, "говорящий получает свою же реплику кружка"
    assert sleeper.identity_id not in told, "спящему реплику кружка не доставляют"
