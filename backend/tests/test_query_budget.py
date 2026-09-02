# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the commonest command costs in round trips (review 2026-08-23, item 3).

The review measured `look` at 45-65 queries and asked for a threshold on that
number, because the cost of this command is not the work the database does --
it is the number of times the server waits for an answer. A ceiling is the only
thing that keeps a helper from quietly turning one query into twenty: nothing
else fails when it does.

Two kinds of ceiling here, and the second is the point of the first:

* **`look` as a whole** -- a budget, deliberately loose. A guard against a new
  fan-out, not a target to shave.
* **the Net's unread count against the size of the world** -- a *shape*.
  Whatever the count costs, it must cost the same for one channel and for ten,
  and the same for authors in two places and in ten. It used to build a full
  view of every channel to add up one integer, and cost three more queries per
  channel a reader had subscribed to: 11 on one channel, 38 on ten, on the
  scene built below. It is three now either way -- five where there are roads,
  and the two extra are the map, read once -- and the whole command went from
  98 to 63.

Both dimensions are measured, because the count grows along two and they are
not the same one: the channels are a `LATERAL` in SQL, the roads are a loop in
Python. A scene with every author standing in the reader's own node proves
nothing about the second -- `road_seconds` returns on `here == there` and the
map is never even read.

The numbers below are ceilings measured on the scenes built here, not promises
about a live world. Raising one is a decision; noticing it has to be raised is
the whole purpose.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import city as town
from src.engine import net, travel, world
from src.models.world import Node
from tests.conftest import Counter
from tests.net_kit import _capital

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)

#: The whole command, on the two scenes below. Two numbers and not one, because
#: they measure different worlds and must not share a margin: a city with no
#: roads in it (65 measured on 2026-09-02) and the same city with two streets
#: (72 -- two of them the Net's map, the rest `look`'s own, see
#: `_citizen_with_channels`). One ceiling over both would sit flush against the
#: larger, and the first honest field added anywhere in `look` would break it;
#: whoever tripped over that would raise the number rather than read it. The
#: margin is not decoration: merging the ballot in the Net tab (D-161) cost
#: `look` two queries the same day these numbers were taken, 63 and 70 before
#: it, and a flush ceiling would have failed the merge instead of the code.
#:
#: Both are ceilings for a **five-node** city. `terra.capital` has thirteen, and
#: the territory grows as land is bought, so neither number is a promise about
#: the live world -- they guard against a new fan-out, not against the old one.
LOOK_BUDGET = 75
LOOK_BUDGET_ON_ROADS = 80

#: What one more channel, or one more place an author writes from, is allowed
#: to add. Zero: the count is a count, not a walk over the reader's world.
PER_CHANNEL_BUDGET = 0


@pytest.fixture(autouse=True)
def _fresh_map():
    """The map in memory belongs to the previous test's world."""
    net.forget_graph()
    yield
    net.forget_graph()


async def _citizen_with_channels(
    session: AsyncSession, catalog: Catalog, *, channels: int, streets: int = 0
) -> uuid.UUID:
    """A citizen of a city who has subscribed to `channels` written channels.

    Two sources of a channel at once -- the city's by citizenship and the
    chosen ones -- with one unread post in each.

    `streets` lays that many roads inside the city and spreads the authors over
    them, so that the scene has a map to read: a street is not a border, and an
    edge between two nodes of one city needs no gate (D-206). With none of them
    everybody stands in the core, `road_seconds` returns on `here == there`, and
    a measurement over this scene never touches the map at all.

    Two streets and not ten, because a street is not free to `look`. Finding a
    city's core means finding the Forerunners' printer (`city.lookup.core`), and
    where the city's own node does not hold one the search asks **every** node
    of the territory what stands in it -- two queries each, and it cannot stop
    at the first, because a precursor's machine outranks an older one. Ten
    streets add twenty queries that have nothing to do with the Net and bury the
    two this scene exists to include. That fan-out is `look`'s own and older
    than this file; it is named here so that a ceiling measured on a small city
    is not mistaken for one that holds on a large one.
    """
    city, core, founder = await _capital(session, catalog)
    reader = await world.create_identity(session, f"Читатель-{uuid.uuid4().hex[:6]}")
    await world.print_body(session, reader, core)
    await town._enroll(session, city, reader.id, why="test")

    delegate = await session.get(Node, city.node_id)
    where = [core]
    for _ in range(streets):
        #: A child of the city's node, as the core is -- not of the core. A node
        #: whose parent is a plot is the shape of a **storey**
        #: (`estate.building.frame`), and a scene that means streets should not
        #: draw them as floors.
        street = await world.create_node(
            session, f"terra.street.{uuid.uuid4().hex[:8]}", "Улица", area_m2=50, parent=delegate
        )
        street.owner_city_id = city.id
        await session.flush()
        await travel.connect(session, where[-1], street, base_seconds=60)
        where.append(street)

    official = await net.city_channel(session, city)
    assert official is not None, "город основан со своим каналом"
    await net.post(session, founder, official.id, "закон сменился", now=NOW)
    for n in range(channels):
        author = await world.create_identity(session, f"Автор-{uuid.uuid4().hex[:6]}")
        await world.print_body(session, author, where[n % len(where)])
        feed = await net.create_channel(session, author, f"Вести {n} {uuid.uuid4().hex[:6]}")
        await net.subscribe(session, reader, feed.id)
        await net.post(session, author, feed.id, "слышали?", now=NOW)
    await session.commit()
    return reader.id


async def _reader_with_authors(session: AsyncSession, *, places: int) -> uuid.UUID:
    """A reader on wild land, with ten channels whose authors stand in `places`.

    Wild land and not a city: an edge across a city's boundary is allowed only
    at its gates (D-206), and this scene is about roads, not doors. One place
    means every author writes from the reader's own node and there is no road
    to measure; ten means ten nodes a minute's walk away, each its own source
    in the map.
    """
    stamp = uuid.uuid4().hex[:8]
    home = await world.create_node(session, f"terra.wild.{stamp}", "Пустошь", area_m2=100)
    reader = await world.create_identity(session, f"Читатель-{stamp}")
    await world.print_body(session, reader, home)

    nodes = [home]
    for n in range(1, places):
        far = await world.create_node(session, f"terra.wild.{stamp}.{n}", "Хутор", area_m2=50)
        await travel.connect(session, home, far, base_seconds=60)
        nodes.append(far)

    for n in range(10):
        author = await world.create_identity(session, f"Автор-{uuid.uuid4().hex[:6]}")
        await world.print_body(session, author, nodes[n % len(nodes)])
        feed = await net.create_channel(session, author, f"Вести {n} {uuid.uuid4().hex[:6]}")
        await net.subscribe(session, reader, feed.id)
        await net.post(session, author, feed.id, "слышали?", now=NOW)
    await session.commit()
    return reader.id


async def _cost(
    session: AsyncSession, constants: Constants, me_id: uuid.UUID, *, at: datetime = NOW
) -> tuple[int, int]:
    """Round trips spent on the unread count, and what it came to.

    The road map is dropped first: where there is a road to measure, reading
    the map back is two queries, and one measurement paying them while the
    other reads a warm map would flatter whichever went second.
    """
    net.forget_graph()
    meter = Counter(session)
    try:
        before = meter.count
        count = await net.unread_posts(session, constants, me_id, now=at)
    finally:
        meter.stop()
    return meter.count - before, count


async def test_the_unread_count_does_not_grow_with_the_channels(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One channel and ten cost the same number of queries.

    The count used to run the channel-list builder and add up the `unread` of
    every view: a subscription, a page of posts, the author's name and the
    right to post, per channel, on every `look`.
    """
    one = await _citizen_with_channels(session, catalog, channels=1)
    thin, thin_count = await _cost(session, constants, one)
    #: The official channel of the city, plus the one subscribed to.
    assert thin_count == 2

    many = await _citizen_with_channels(session, catalog, channels=10)
    fat, fat_count = await _cost(session, constants, many)
    assert fat_count == 11

    grew = fat - thin
    assert grew <= PER_CHANNEL_BUDGET * 9, (
        f"счёт непрочитанного стоит {thin} запросов на одном канале "
        f"и {fat} на десяти: {grew} лишних на девять каналов"
    )


async def test_the_unread_count_does_not_grow_with_the_roads(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Ten authors in ten places cost what ten authors in two places do.

    The delay cannot be asked of the database, so the count works it out per
    author node -- and that loop is the last thing in it that grows with the
    world. It has to grow in arithmetic and not in queries: the map is read
    once, and every node after that is answered out of it (`road`).

    Two places against ten, and not one against ten, because a reader whose
    authors all stand where they do reads no map at all -- `road_seconds`
    returns on `here == there`. Between one place and two there is a step of
    two queries, and it is the map itself; from two onwards there must be no
    step at all, and that is the thing worth pinning.
    """
    #: Late enough that a minute of road has been walked -- otherwise the far
    #: scene would be counting nothing, and prove nothing by costing little.
    arrived = NOW + timedelta(hours=1)

    near_by = await _reader_with_authors(session, places=2)
    near, near_count = await _cost(session, constants, near_by, at=arrived)
    assert near_count == 10

    scattered = await _reader_with_authors(session, places=10)
    far, far_count = await _cost(session, constants, scattered, at=arrived)
    assert far_count == 10

    assert far - near <= PER_CHANNEL_BUDGET * 8, (
        f"счёт стоит {near} запросов, когда авторы в двух узлах, и {far}, когда в десяти"
    )


async def _look_cost(factory, me_id: uuid.UUID) -> int:
    from src.api.commands.look import _look

    net.forget_graph()
    async with factory() as db:
        meter = Counter(db)
        try:
            before = meter.count
            await _look({"identity_id": me_id}, db, {"cmd": "look"})
        finally:
            meter.stop()
        return meter.count - before


async def test_look_stays_within_its_budget(
    session: AsyncSession, constants: Constants, catalog: Catalog, factory
) -> None:
    """The whole command, on a citizen with ten channels, under the ceiling.

    Measured twice, and the second time is the one that had to be added: with
    every author in the reader's own node the Net's road is never walked and the
    map never read, so a ceiling taken there would guard a `look` two queries
    and one code path shorter than the real one.

    It does not make the ceiling a measurement of the real command. The second
    scene is two streets wide for the reason `_citizen_with_channels` gives, so
    both numbers still describe a five-node city; what they guard is a new
    fan-out appearing, not the size of the old one.
    """
    together = await _citizen_with_channels(session, catalog, channels=10)
    spent = await _look_cost(factory, together)
    assert spent <= LOOK_BUDGET, f"look стоит {spent} запросов при потолке {LOOK_BUDGET}"

    scattered = await _citizen_with_channels(session, catalog, channels=10, streets=2)
    on_roads = await _look_cost(factory, scattered)
    assert on_roads <= LOOK_BUDGET_ON_ROADS, (
        f"look по сцене с дорогами стоит {on_roads} запросов при потолке {LOOK_BUDGET_ON_ROADS}"
    )
