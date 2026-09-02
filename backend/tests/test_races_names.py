# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once reaching for the same name.

One of the race files (see `test_races.py` for the family's method). Cut out
of `test_races_ground.py`, which is about the contested *place*: a name is not
ground, and that file had grown past the bar besides.

What is contested here is a word. A city's name is unique in the world, a
channel's is unique in the Net, and a city's name becomes its channel's
(D-284) -- so the two rules are one chain, held at the bottom by
`uq_city_name_lower` and `uq_net_channel_name_lower`. Both doors ask before
they write, and two writers pass that question together: only the index tells
them apart. The loser must come away with words, not with a server error.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.identity import Body


async def test_two_foundings_of_one_name_leave_one_refused(
    factory: async_sessionmaker[AsyncSession],
    constants,
    catalog,
) -> None:
    """One name, one city, even when two people type it in the same second.

    `city.establish` asks whether the name is taken before it founds, and two
    foundings pass that question together: each selects nothing, each inserts,
    and without `uq_city_name_lower` the world ends with two cities of one
    name -- and two official channels of one name, which `net.channel.create`
    refuses from anybody who types it.

    The loser must not die of the refusal either. The insert sits under a
    savepoint, so the violation comes back as `city-found-name-taken` -- the
    same words the pre-check would have said -- rather than as a server error
    on a legitimate move. The first session holds its insert uncommitted for
    the window; the second then provably selects nothing and queues at the
    index until the winner commits.
    """
    from city_kit import _resident
    from src.engine import city as town
    from src.engine import world
    from src.models.city import City
    from src.models.world import Layer

    stamp = uuid.uuid4().hex[:8]
    name = f"Одноимённый-{stamp}"

    async def _ready(where: str) -> uuid.UUID:
        """A place with the four buildings up and somebody standing in it."""
        async with factory() as db, db.begin():
            planet = await world.create_node(
                db, f"terra.{where}.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
            )
            place = await world.create_node(
                db,
                f"terra.{where}.{stamp}.ground",
                "Место под город",
                area_m2=400,
                layer=Layer.PLANET,
                parent=planet,
                properties={"wild": True},
            )
            _, body = await _resident(db, place, f"Основатель-{where}-{stamp}")
            yard = await world.node_container(db, place)
            from src.engine import death
            from src.engine import energy as power

            for machine in (death.PRINTER, town.HALL, "market_terminal", power.WHEEL):
                await world.grant_item(db, yard, machine, quality=60, origin="тест")
            return body.id

    #: Two places, so that what the index refuses is the name and not the node.
    here, there = await _ready("first"), await _ready("second")
    founded = asyncio.Event()

    async def found_it(body_id: uuid.UUID, first: bool) -> None:
        if not first:
            await founded.wait()
        async with factory() as db, db.begin():
            body = await db.get(Body, body_id)
            assert body is not None
            await town.establish(db, constants, catalog, body, name)
            if first:
                founded.set()
                await asyncio.sleep(0.2)

    outcomes = await asyncio.gather(
        found_it(here, True), found_it(there, False), return_exceptions=True
    )
    refused = [one for one in outcomes if isinstance(one, town.CityError)]
    assert len(refused) == 1, f"второму имя достаться не должно: {outcomes}"
    assert refused[0].key == "city-found-name-taken", (
        f"отказ, а не поломка сервера: {refused[0].key}"
    )

    async with factory() as db:
        rows = (
            (await db.execute(select(City).where(func.lower(City.name) == name.lower())))
            .scalars()
            .all()
        )
        assert len(rows) == 1, "город с этим именем удвоился"


async def test_two_channels_of_one_name_leave_one_refused(
    factory: async_sessionmaker[AsyncSession],
    catalog,
) -> None:
    """One name, one channel, even when two people type it in the same second.

    `net.create_channel` asks whether the name is taken before it inserts, and
    two writers pass that question together: each selects nothing, each adds a
    row, and without `uq_net_channel_name_lower` the Net ends with two channels
    it draws identically in every list -- and which nobody could have created
    by hand, because the very check they slipped past forbids it.

    The loser must not die of it either: the insert sits under a savepoint, so
    the violation comes back as `net-channel-exists` -- the same words the
    pre-check would have said -- and not as a server error on a legitimate
    move. The first session holds its row uncommitted for the window; the
    second then provably selects nothing and queues at the index.
    """
    from city_kit import _resident
    from net_kit import _capital
    from src.engine import net
    from src.models.identity import Identity
    from src.models.net import NetChannel

    stamp = uuid.uuid4().hex[:8]
    name = f"Тёзка-{stamp}"

    #: One city, two people in it: the racers only need identities, and two
    #: capitals would collide on the city's own name instead.
    async with factory() as db, db.begin():
        _city, core, founder = await _capital(db, catalog)
        other, _body = await _resident(db, core, f"Второй-{stamp}")
        here, there = founder.id, other.id
    made = asyncio.Event()

    async def take(identity_id: uuid.UUID, first: bool) -> None:
        if not first:
            await made.wait()
        async with factory() as db, db.begin():
            me = await db.get(Identity, identity_id)
            assert me is not None
            await net.create_channel(db, me, name)
            if first:
                made.set()
                await asyncio.sleep(0.2)

    outcomes = await asyncio.gather(take(here, True), take(there, False), return_exceptions=True)
    refused = [one for one in outcomes if isinstance(one, net.NetError)]
    assert len(refused) == 1, f"второму имя достаться не должно: {outcomes}"
    assert refused[0].key == "net-channel-exists", f"отказ, а не поломка сервера: {refused[0].key}"

    async with factory() as db:
        kept = (
            await db.execute(
                select(func.count())
                .select_from(NetChannel)
                .where(func.lower(NetChannel.name) == name.lower())
            )
        ).scalar()
        assert kept == 1, "канал с этим именем удвоился"
