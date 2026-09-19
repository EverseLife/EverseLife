# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two hulls meeting in orbit round one planet (D-354, wave 3).

With no node above a planet, a hull in orbit is met the way any hull is --
the helm comes to rest beside it -- only on an arc round the planet rather
than the straight profile of the deep, which aimed through it. Pinned: the
console offers a slider of such arcs, each drawn round the planet's centre;
the order keeps the arc so; the tick flies it to the hold for about its
price; an hour off the slider is refused; and the hold keeps the pair's
momentum instead of handing the chaser the other's speed for nothing.
"""

from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ship_kit import PARK_HEADING, _events, _flown, _hull, _port
from src import sky
from src.constants import Catalog, Constants
from src.engine import ship
from src.engine.ship import hold, sim
from src.models.event import EventKind
from src.models.world import Planet


async def _pair(session: AsyncSession, constants: Constants, catalog: Catalog):
    """Two hulls of two owners on Terra's circle, half a lap apart."""
    home = await _port(session, name="Космодром столицы")
    chaser, chaser_owner = await _hull(
        session, constants, catalog, home, fuel=5000, heading=PARK_HEADING
    )
    other, other_owner = await _hull(
        session, constants, catalog, home, fuel=5000, heading=PARK_HEADING + math.pi
    )
    since = max(chaser.sky_at, other.sky_at) + timedelta(minutes=1)
    return chaser, chaser_owner, other, other_owner, since


async def test_a_hull_in_orbit_is_met_on_an_arc_round_the_planet(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    chaser, chaser_owner, other, other_owner, since = await _pair(session, constants, catalog)
    assert await sim.orbiting(session, constants, other, now=since) is not None

    forecast = await ship.forecast(session, constants, catalog, chaser, other, now=since)
    samples = forecast["samples"]
    assert len(samples) > 3, "ползунок, а не одна цена"
    assert all(one["around"] == Planet.TERRA.value for one in samples)
    #: Round the planet's centre, a quarter of a unit across -- not a tenth.
    assert all(abs(x) < 1 and abs(y) < 1 for one in samples for x, y in one["trace"])
    assert [one["dv"] for one in samples] == sorted((one["dv"] for one in samples), reverse=True)

    chosen = next(one for one in samples if one["hours"] >= 5)
    await ship.fly(
        session, constants, catalog, chaser_owner, chaser, other, hours=chosen["hours"], now=since
    )
    assert chaser.course is not None and chaser.course["around"] == Planet.TERRA.value
    flight = (await ship.profile(session, constants, catalog, chaser))["flight"]
    assert flight["around"] == Planet.TERRA.value

    before = await ship.fuel_aboard(session, constants, catalog, chaser)
    await _flown(
        session,
        constants,
        catalog,
        chaser,
        since=since,
        until=since + timedelta(hours=chosen["hours"]),
        slack=timedelta(hours=6),
    )
    assert chaser.held_ship_id == other.id, "встал рядом и держится"
    told = await _events(session, EventKind.SHIP_HELD)
    assert {one.actor_identity_id for one in told} == {
        chaser_owner.identity_id,
        other_owner.identity_id,
    }
    burnt = before - await ship.fuel_aboard(session, constants, catalog, chaser)
    assert burnt == pytest.approx(chosen["fuel"], rel=0.15), "за свою цену"


async def test_an_hour_off_the_slider_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    chaser, chaser_owner, other, _, since = await _pair(session, constants, catalog)
    forecast = await ship.forecast(session, constants, catalog, chaser, other, now=since)
    offered = {one["hours"] for one in forecast["samples"]}
    odd = min(offered) + 0.37
    assert odd not in offered
    with pytest.raises(ship.NoArc) as refused:
        await ship.fly(
            session, constants, catalog, chaser_owner, chaser, other, hours=odd, now=since
        )
    assert refused.value.key == "ship-hours-out-of-range"
    assert chaser.course is None


async def test_the_hold_keeps_the_momentum_of_the_pair(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Come to rest within the hold's speed of the other: the pair moves at
    the speed the two masses average to, and the other's coast is counted
    afresh from it -- not the chaser handed the other's speed for nothing."""
    chaser, _, other, _, since = await _pair(session, constants, catalog)
    found = await sim.state_at(session, constants, other, now=since, exact=True)
    assert found is not None
    r, v, _ = found
    offset = (0.3, -0.2)
    mine = (v[0] + offset[0], v[1] + offset[1])
    before = dict(other.forecast or {})

    await hold.begin(session, constants, catalog, chaser, other, r, mine, now=since)

    light = await ship.mass(session, constants, catalog, chaser)
    heavy = await ship.mass(session, constants, catalog, other)
    share = light / (light + heavy)
    want = np.array(v) + share * np.array(offset)
    assert np.array([other.sky_vx, other.sky_vy]) == pytest.approx(want, abs=1e-9)
    assert (chaser.sky_vx, chaser.sky_vy) == (other.sky_vx, other.sky_vy)
    assert (chaser.sky_x, chaser.sky_y) == pytest.approx(r, abs=1e-12)
    assert chaser.held_ship_id == other.id
    assert other.forecast is not None and other.forecast != before, "путь пары пересчитан"
    assert other.forecast["since"] == since.isoformat()
    assert sky.bound_to(
        await sim.system(session, constants),
        await ship.sky_days(session, since),
        (other.sky_x, other.sky_y),
        (other.sky_vx, other.sky_vy),
    ), "пара на орбите"
