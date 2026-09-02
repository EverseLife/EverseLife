# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The Net: correspondence and channels (D-222).

Checked is what it is built this way for:

* a letter is kept: it is read back later, and an empty thread is a thread;
* the delay is the road times the constant: together -- at once, three edges
  away -- three edges' worth; the reader does not see the letter before it arrives;
* between planets the road is the passage by the sky; no road at all is the sea;
* a body is what one writes with: an identity without one reads but not writes;
* the city's channel is official, its power writes there and citizens read it
  whether they chose to or not; a player's channel is written by its author only;
* a post arrives to every reader by their own road.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import net, travel, world
from src.models.city import Power
from src.models.identity import BodyState
from src.models.net import NetChannel, NetSubscription
from src.models.world import Layer, Planet
from tests.net_kit import _capital

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


async def _place(session: AsyncSession, name: str = "room", *, planet: Planet = Planet.TERRA):
    return await world.create_node(
        session, f"{planet.value}.{name}.{uuid.uuid4().hex[:8]}", name, planet=planet, area_m2=50
    )


async def _person(session: AsyncSession, node, name: str = "Гость"):
    identity = await world.create_identity(session, f"{name}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    return identity, body


@pytest.fixture(autouse=True)
def _fresh_map():
    """The map in memory belongs to the previous test's world."""
    net.forget_graph()
    yield
    net.forget_graph()


# --- the road ----------------------------------------------------------------


async def test_together_means_no_delay(session: AsyncSession, constants: Constants) -> None:
    room = await _place(session)
    one, _ = await _person(session, room)
    other, _ = await _person(session, room)
    thread = await net.open_thread(session, one, other)
    letter = await net.write(session, constants, one, thread.id, "привет", now=NOW)
    assert letter.delivered_at == NOW


async def test_delay_is_the_road_times_the_constant(
    session: AsyncSession, constants: Constants
) -> None:
    a = await _place(session, "a")
    b = await _place(session, "b")
    c = await _place(session, "c")
    await travel.connect(session, a, b, base_seconds=600)
    await travel.connect(session, b, c, base_seconds=600)
    one, _ = await _person(session, a)
    other, _ = await _person(session, c)
    thread = await net.open_thread(session, one, other)
    letter = await net.write(session, constants, one, thread.id, "далеко", now=NOW)
    road = await net.road_seconds(session, constants, a.id, c.id, now=NOW)
    assert road == pytest.approx(1200 * constants[R.ROAD_ROAD_MULTIPLIER])
    expected = timedelta(seconds=road * constants[R.COMM_DELAY_PER_SECOND])
    assert letter.delivered_at == NOW + expected
    assert expected > timedelta(0)

    #: The reader is told on arrival, not on sending (D-226): a job waits for
    #: the road, addressed to the reader alone.
    from src.models.job import Job, JobKind

    jobs = (
        (await session.execute(select(Job).where(Job.kind == JobKind.NET_DELIVER.value)))
        .scalars()
        .all()
    )
    assert [(j.run_at, j.payload) for j in jobs] == [
        (letter.delivered_at, {"identity": str(other.id), "event": "net.letter"})
    ]


async def test_reader_sees_the_letter_only_when_it_arrives(
    session: AsyncSession, constants: Constants
) -> None:
    a = await _place(session, "a")
    b = await _place(session, "b")
    await travel.connect(session, a, b, base_seconds=3600)
    one, _ = await _person(session, a)
    other, _ = await _person(session, b)
    thread = await net.open_thread(session, one, other)
    letter = await net.write(session, constants, one, thread.id, "в пути", now=NOW)

    #: The writer sees their own at once.
    own = await net.read_thread(session, one.id, thread.id, now=NOW)
    assert [x.text for x in own] == ["в пути"]
    #: The reader -- nothing yet, and nothing counted.
    assert await net.read_thread(session, other.id, thread.id, now=NOW) == []
    assert await net.unread_letters(session, other.id, now=NOW) == 0
    later = letter.delivered_at
    assert await net.unread_letters(session, other.id, now=later) == 1
    assert [x.text for x in await net.read_thread(session, other.id, thread.id, now=later)] == [
        "в пути"
    ]
    #: Reading marks it read.
    assert await net.unread_letters(session, other.id, now=later) == 0


async def test_no_road_counts_as_the_sea(session: AsyncSession, constants: Constants) -> None:
    a = await _place(session, "island")
    b = await _place(session, "shore")
    sea = (constants[R.SHIP_ASCENT_HOURS] + constants[R.SHIP_DESCENT_HOURS]) * 3600
    assert await net.road_seconds(session, constants, a.id, b.id, now=NOW) == pytest.approx(sea)


async def test_between_planets_the_road_is_the_passage(
    session: AsyncSession, constants: Constants
) -> None:
    a = await _place(session, "port", planet=Planet.TERRA)
    b = await _place(session, "port", planet=Planet.PYROXIS)
    hours = float(constants[R.ORBIT_LONGEST_DAYS]) * 24
    #: No orbits in this world: the slow end of the slider, never the cheap one.
    assert await net.road_seconds(session, constants, a.id, b.id, now=NOW) == pytest.approx(
        hours * 3600
    )


async def test_a_laid_road_shortens_the_delay(session: AsyncSession, constants: Constants) -> None:
    a = await _place(session, "a")
    b = await _place(session, "b")
    before = await net.road_seconds(session, constants, a.id, b.id, now=NOW)
    await travel.connect(session, a, b, base_seconds=10)
    after = await net.road_seconds(session, constants, a.id, b.id, now=NOW)
    assert after < before


# --- who writes --------------------------------------------------------------


async def test_without_a_body_one_reads_but_does_not_write(
    session: AsyncSession, constants: Constants
) -> None:
    room = await _place(session)
    one, body = await _person(session, room)
    other, _ = await _person(session, room)
    thread = await net.open_thread(session, one, other)
    await net.write(session, constants, other, thread.id, "ты жив?", now=NOW)
    body.state = BodyState.DEAD
    await session.flush()
    assert [x.text for x in await net.read_thread(session, one.id, thread.id, now=NOW)] == [
        "ты жив?"
    ]
    with pytest.raises(net.NoBody):
        await net.write(session, constants, one, thread.id, "нет", now=NOW)


async def test_an_empty_thread_is_kept(session: AsyncSession) -> None:
    room = await _place(session)
    one, _ = await _person(session, room)
    other, _ = await _person(session, room)
    thread = await net.open_thread(session, one, other)
    again = await net.open_thread(session, other, one)
    assert again.id == thread.id
    views = await net.threads(session, one.id, now=NOW)
    assert [v.who for v in views] == [other.name]
    assert views[0].preview is None


async def test_no_letters_to_oneself(session: AsyncSession) -> None:
    room = await _place(session)
    one, _ = await _person(session, room)
    with pytest.raises(net.NetError):
        await net.open_thread(session, one, one)


# --- channels ----------------------------------------------------------------


async def test_the_channel_is_founded_with_the_city(
    session: AsyncSession, catalog: Catalog
) -> None:
    """A city has its voice from the founding, before anybody looks.

    It used to be opened by the first read of it, and `look` reads it for the
    tab's count: the city's channel was a row an INSERT-free command wrote.
    Nobody has read anything here -- the channel is there all the same, under
    the city's own name.
    """
    city, _, _ = await _capital(session, catalog)
    channel = await net.city_channel(session, city)
    assert channel is not None and channel.name == city.name


async def test_city_channel_is_official_and_its_power_writes(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core, founder = await _capital(session, catalog)
    assert Power.CHANNEL.value in await town.powers_of(session, founder.id, city)
    views = await net.channels(session, constants, founder.id, now=NOW)
    #: By the city's own name, not a literal: the kit stamps it, because a
    #: city name is unique across the world and so is its channel's.
    assert [(v.official, v.writable, v.implied, v.by) for v in views] == [
        (True, True, True, city.name)
    ]
    channel = await net.city_channel(session, city)
    assert channel is not None
    await net.post(session, founder, channel.id, "закон сменился", now=NOW)

    #: A citizen reads it without subscribing, and cannot drop it.
    citizen, _ = await _person(session, core, "Гражданин")
    await town._enroll(session, city, citizen.id, why="test")
    mine = await net.channels(session, constants, citizen.id, now=NOW)
    assert [(v.name, v.unread, v.writable) for v in mine] == [(city.name, 1, False)]
    with pytest.raises(net.NetError):
        await net.unsubscribe(session, citizen, channel.id)
    with pytest.raises(net.NotAllowed):
        await net.post(session, citizen, channel.id, "а я против", now=NOW)

    #: A stranger sees nothing until they subscribe.
    stranger, _ = await _person(session, core, "Приезжий")
    assert await net.channels(session, constants, stranger.id, now=NOW) == []
    await net.subscribe(session, stranger, channel.id)
    assert [v.name for v in await net.channels(session, constants, stranger.id, now=NOW)] == [
        city.name
    ]
    await net.unsubscribe(session, stranger, channel.id)
    assert await net.channels(session, constants, stranger.id, now=NOW) == []


async def test_the_city_channel_is_named_after_the_city(
    session: AsyncSession, catalog: Catalog
) -> None:
    """The official channel carries the city's name, not a trimming of it.

    This is the link that made an unbounded city name a Net defect: whatever
    the city is called, the channel is called the same, and nothing here
    shortens it. The ceiling that keeps such a name inside `NET_NAME_LIMIT`
    is the founding door's, and it is pinned where it is enforced --
    `test_city_founding.test_city_name_is_bounded`, which founds through
    `establish` and follows the name all the way here.
    """
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session,
        f"terra.named.{stamp}",
        "Место",
        area_m2=400,
        layer=Layer.PLANET,
        parent=planet,
    )
    city = await town.found(session, catalog, delegate, f"Длинноимённый-{stamp}")

    channel = await net.city_channel(session, city)
    assert channel is not None, "у города есть официальный канал"
    assert channel.name == city.name, "канал зовётся городом, а не обрезком"


async def test_a_players_channel_is_written_by_its_author_only(
    session: AsyncSession, constants: Constants
) -> None:
    room = await _place(session)
    author, _ = await _person(session, room, "Автор")
    reader, _ = await _person(session, room, "Читатель")
    channel = await net.create_channel(session, author, "Вести с полей", "про урожай")
    with pytest.raises(net.NetError):
        await net.create_channel(session, reader, "вести с полей")
    with pytest.raises(net.NotAllowed):
        await net.post(session, reader, channel.id, "чужое", now=NOW)
    await net.post(session, author, channel.id, "рожь взошла", now=NOW)

    found = await net.find_channels(session, "вести", me_id=reader.id)
    assert [(c.name, by) for c, by in found] == [("Вести с полей", author.name)]
    await net.subscribe(session, reader, channel.id)
    _, posts = await net.read_channel(session, constants, reader.id, channel.id, now=NOW)
    assert [p.text for p in posts] == ["рожь взошла"]
    assert await net.unread_posts(session, constants, reader.id, now=NOW) == 0


async def test_a_post_arrives_to_each_reader_by_their_own_road(
    session: AsyncSession, constants: Constants
) -> None:
    a = await _place(session, "a")
    b = await _place(session, "b")
    await travel.connect(session, a, b, base_seconds=3600)
    author, _ = await _person(session, a, "Автор")
    near, _ = await _person(session, a, "Рядом")
    far, _ = await _person(session, b, "Далеко")
    channel = await net.create_channel(session, author, "Слухи")
    for reader in (near, far):
        await net.subscribe(session, reader, channel.id)
    await net.post(session, author, channel.id, "слышали?", now=NOW)

    _, here = await net.read_channel(session, constants, near.id, channel.id, now=NOW)
    assert [p.text for p in here] == ["слышали?"]
    _, there = await net.read_channel(session, constants, far.id, channel.id, now=NOW)
    assert there == []
    road = await net.road_seconds(session, constants, a.id, b.id, now=NOW)
    later = NOW + timedelta(seconds=road * constants[R.COMM_DELAY_PER_SECOND])
    _, there = await net.read_channel(session, constants, far.id, channel.id, now=later)
    assert [p.text for p in there] == ["слышали?"]


async def test_the_unread_count_waits_for_the_road(
    session: AsyncSession, constants: Constants
) -> None:
    """The tab's count obeys the same delay the channel does.

    It is counted in SQL and not by building the channel list (review
    2026-08-23, item 3), and the road is the one thing SQL does not know: the
    count asks per author node how old a post must be to have arrived. Two
    authors, two roads, one reader -- the near word is counted and the far one
    is not, until it is.
    """
    a = await _place(session, "a")
    b = await _place(session, "b")
    await travel.connect(session, a, b, base_seconds=3600)
    near_author, _ = await _person(session, a, "Рядом")
    far_author, _ = await _person(session, b, "Далеко")
    reader, _ = await _person(session, a, "Читатель")
    for author, name in ((near_author, "Близкие"), (far_author, "Дальние")):
        channel = await net.create_channel(session, author, f"{name} вести")
        await net.subscribe(session, reader, channel.id)
        await net.post(session, author, channel.id, "слышали?", now=NOW)

    assert await net.unread_posts(session, constants, reader.id, now=NOW) == 1
    road = await net.road_seconds(session, constants, a.id, b.id, now=NOW)
    later = NOW + timedelta(seconds=road * constants[R.COMM_DELAY_PER_SECOND])
    assert await net.unread_posts(session, constants, reader.id, now=later) == 2


async def test_the_unread_count_takes_only_the_readers_channels(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Own, chosen and the city's -- and nothing else; read is not unread.

    The three sources are one `UNION` in the counting query now (`_my_channels`),
    so each is worth a row of its own here: a channel nobody subscribed to must
    not be counted, and neither must a channel already read.

    The last line is the seam. `channels` picks the reader's channels its own
    way, in Python, and only the count goes through `_my_channels`; nothing but
    this would notice the two rules drifting apart, and a badge that disagrees
    with the rows under it is exactly what a reader adds up by hand.
    """
    city, core, founder = await _capital(session, catalog)
    reader, _ = await _person(session, core, "Гражданин")
    await town._enroll(session, city, reader.id, why="test")
    official = await net.city_channel(session, city)
    assert official is not None, "город основан со своим каналом"
    await net.post(session, founder, official.id, "закон сменился", now=NOW)
    #: The city's word alone: implied by citizenship, never subscribed to. The
    #: absence of the row is the point and is asserted rather than assumed --
    #: the count reaches `read_at` through an **outer** join, and a citizen who
    #: has never opened the channel has nothing to join to. Everyone who ever
    #: clicked has a row, so an inner join would pass every test but this one.
    assert (
        await session.scalar(
            select(func.count())
            .select_from(NetSubscription)
            .where(NetSubscription.identity_id == reader.id)
        )
        == 0
    )
    assert await net.unread_posts(session, constants, reader.id, now=NOW) == 1

    #: One's own channel counts too -- including, as it always has, what one
    #: wrote there oneself. A letter to oneself is impossible, a post is not.
    own = await net.create_channel(session, reader, "Мои вести")
    await net.post(session, reader, own.id, "я тут", now=NOW)
    assert await net.unread_posts(session, constants, reader.id, now=NOW) == 2

    #: A stranger's channel is not counted until it is chosen.
    stranger, _ = await _person(session, core, "Приезжий")
    foreign = await net.create_channel(session, stranger, "Чужие вести")
    await net.post(session, stranger, foreign.id, "не для всех", now=NOW)
    assert await net.unread_posts(session, constants, reader.id, now=NOW) == 2
    await net.subscribe(session, reader, foreign.id)
    assert await net.unread_posts(session, constants, reader.id, now=NOW) == 3

    #: Reading takes the channel out of the count, and only that channel.
    await net.read_channel(session, constants, reader.id, official.id, now=NOW)
    assert await net.unread_posts(session, constants, reader.id, now=NOW) == 2

    #: The tab and the rows behind it are one count, and say so.
    views = await net.channels(session, constants, reader.id, now=NOW)
    assert sum(view.unread for view in views) == await net.unread_posts(
        session, constants, reader.id, now=NOW
    )


async def test_the_seed_founds_a_city_whose_name_a_channel_holds_without_one(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The seed's door never refuses over a name -- it gives up the channel.

    A name is one channel's (D-284), and the city's official channel takes the
    city's name, so a player's channel can stand where a vault city is about
    to be founded. Refusing here was the obvious answer and the wrong one:
    `found` is the seed's door, the seed is one container the whole deploy
    waits on, and a refusal would let anybody stop the next deploy of the
    server by taking a name (OQ-118). So the city is raised and the channel is
    what is missing -- a state the Net already reads (`city_channel` -> None).
    """
    _city, _core, founder = await _capital(session, catalog)
    name = f"Тёзка-{uuid.uuid4().hex[:8]}"
    await net.create_channel(session, founder, name)
    await session.flush()

    place = await world.create_node(
        session, f"terra.namesake.{uuid.uuid4().hex[:8]}", "Место", area_m2=400
    )
    town_of_that_name = await town.found(session, catalog, place, name)
    await session.flush()

    #: The city stands, whole -- not half of one rolled back.
    assert town_of_that_name.name == name
    assert await net.city_channel(session, town_of_that_name) is None
    #: And the Net still holds exactly one channel of that name: the player's.
    kept = (
        await session.execute(
            select(func.count())
            .select_from(NetChannel)
            .where(func.lower(NetChannel.name) == name.lower())
        )
    ).scalar()
    assert kept == 1


async def test_a_channel_name_is_refused_whatever_the_case(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Case is not what tells two channels apart -- `uq_net_channel_name_lower`
    is, and `create_channel` asks the same way it does."""
    _city, _core, founder = await _capital(session, catalog)
    name = f"Новоград-{uuid.uuid4().hex[:8]}"
    await net.create_channel(session, founder, name)
    await session.flush()

    with pytest.raises(net.NetError) as refusal:
        await net.create_channel(session, founder, name.upper())
    assert refusal.value.key == "net-channel-exists"
