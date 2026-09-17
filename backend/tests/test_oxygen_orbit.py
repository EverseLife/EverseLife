# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Oxygen in orbit: the void over any planet, whatever hangs below it
(D-233, D-245).

An orbital node carries the planet it circles, and that is the whole trap: a
place asked by its planet alone reads the orbit of Terra as Terran air. The
bench is `test_oxygen.py`'s; the orbit is laid here as the seed lays it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oxygen_kit import _cylinder, _ground, _hull, _port, _sphere, _suited
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import oxygen, ship, travel, world
from src.models.identity import BodyState
from src.models.job import Job, JobKind, JobState
from src.models.world import Layer, Node, Planet, Surface
from src.units import ROUND_AMOUNT


async def _orbit(session: AsyncSession, sphere: Node) -> Node:
    """The void over a planet, laid under its sphere as the seed lays it (D-245):
    carrying the planet it circles, and none of its air."""
    return await world.create_node(
        session,
        ship.orbit_key(sphere.planet),
        f"Орбита {sphere.name}",
        area_m2=1,
        planet=sphere.planet,
        layer=Layer.SPACE,
        parent=sphere,
        properties={ship.ORBIT_NODE: True},
    )


async def test_the_sweep_and_the_reading_agree_on_where_there_is_no_air(
    session: AsyncSession,
) -> None:
    """The tick selects by `without_air` and everything else asks `free_air`.

    Two readings of one rule, and they drifted apart once already: the tick
    asked the planet alone and never saw the void over Terra. Every place a
    body can stand outside a hull is asked of both.
    """
    terra = await _sphere(session, Planet.TERRA, airless=False)
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    places = [
        await _ground(session, Planet.TERRA, terra),
        await _ground(session, Planet.PYROXIS, pyroxis),
        await _orbit(session, terra),
        await _orbit(session, pyroxis),
    ]
    swept = set(
        (
            await session.execute(
                select(Node.id).where(
                    Node.id.in_([place.id for place in places]),
                    oxygen.without_air(await oxygen.airless_planets(session)),
                )
            )
        )
        .scalars()
        .all()
    )
    for place in places:
        assert (place.id in swept) is not await oxygen.free_air(session, place), place.key


async def test_the_void_over_a_planet_with_air_is_breathed_and_kills(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """An orbit is the void whatever hangs below it (D-233, D-245).

    An orbital node carries the planet it circles, and the tick used to pick
    the bodies it charged by that planet alone. Over Terra, whose ground has
    air, a suited body stepped off a hull moored in orbit and stood there for
    ever: its cylinder was charged only when it walked on, and it neither
    choked nor died -- while the door it passed and the gauge it was shown
    both called the place the void. The only planet in this world has air, so
    the sweep is also pinned to run where no planet is airless at all.
    """
    terra = await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    orbit = await _orbit(session, terra)
    #: Moored in orbit (D-245, D-289): the one gangway runs from the connector
    #: to the orbital node, and the pier is left behind.
    await travel.disconnect(session, port, connector)
    await travel.connect(session, orbit, connector, base_seconds=1, surface=Surface.PAVED)
    vessel.docked_node_id = orbit.id
    await _suited(session, constants, catalog, body)
    #: An hour and a half of air: the first hour covered, the second short.
    draw = constants[R.OXYGEN_BODY_DRAW]
    await _cylinder(session, body, round(1.5 * draw, ROUND_AMOUNT))
    await session.flush()

    #: The step off, as a player takes it: the door lets a suit with a
    #: cylinder through, and the leg ends on the orbital node.
    started = datetime.now(UTC)
    await travel.depart(session, constants, body, orbit, now=started)
    leg = (
        await session.execute(
            select(Job).where(
                Job.body_id == body.id,
                Job.kind == JobKind.TRAVEL_LEG.value,
                Job.state == JobState.PENDING,
            )
        )
    ).scalar_one()
    await travel.arrive(session, leg)
    assert body.node_id == orbit.id, "тело не вышло на орбиту"
    assert body.air_at == started, "шаг с борта не рассчитал дыхание"

    hour = timedelta(hours=1)
    assert await oxygen.tick_bodies(session, constants, catalog, now=started + hour) == 0
    assert await oxygen.carried(session, body) == pytest.approx(0.5 * draw, abs=0.001), (
        "час на орбите не списан с баллона"
    )
    assert body.choking_since is None

    #: The second hour finds half of it: drained, marked, and alive.
    assert await oxygen.tick_bodies(session, constants, catalog, now=started + 2 * hour) == 0
    assert await oxygen.carried(session, body) == pytest.approx(0, abs=0.001)
    assert body.choking_since is not None, "сухой баллон не поставил отсчёт"

    #: And the third, begun on nothing, ends the body.
    assert await oxygen.tick_bodies(session, constants, catalog, now=started + 3 * hour) == 1
    assert body.state is not BodyState.ALIVE, "сухое тело на орбите не задохнулось"
