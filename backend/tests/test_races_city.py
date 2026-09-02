# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once on the same city.

One of the race files (see `test_races.py` for the family's method). Its own
file rather than another hundred lines of `test_races_ground.py`, which is
over the 800 the quality bar allows and is about the ground, not the polity
standing on it -- the same way the bank's and citizenship's races took files
of their own.

What is contested here is a city's name: it is one city's across the world,
and it becomes the name of that city's channel in the Net, so two foundings
that agreed on a name would hand out two channels the Net calls one.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.city import City
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
    #: And that it is the index speaking, not the question `establish` asks
    #: first. Both refuse with one key, so the test would pass on the
    #: pre-check alone -- and then it would be proving nothing about the race
    #: it is named for. Only the index path carries the violation as a cause.
    assert isinstance(refused[0].__cause__, IntegrityError), (
        f"отказ пришёл от индекса, а не от предпроверки: {refused[0].__cause__!r}"
    )

    async with factory() as db:
        rows = (
            (await db.execute(select(City).where(func.lower(City.name) == name.lower())))
            .scalars()
            .all()
        )
        assert len(rows) == 1, "город с этим именем удвоился"
