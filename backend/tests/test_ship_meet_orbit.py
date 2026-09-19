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

from ship_kit import PARK_HEADING, _events, _flown, _hull, _planet, _port
from src import sky
from src.constants import Catalog, Constants
from src.engine import ship
from src.engine.ship import hold, sim
from src.models.event import EventKind
from src.models.world import Node, Planet

#: Fuel enough for a hull that is only ever a target.
FUEL_FOR_TARGET = 200.0


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
    assert await sim.orbiting(session, constants, other) is not None

    forecast = await ship.forecast(session, constants, catalog, chaser, other, now=since)
    samples = forecast["samples"]
    assert len(samples) > 3, "ползунок, а не одна цена"
    assert forecast["around"] == Planet.TERRA.value
    assert all("around" not in one for one in samples), "once for the slider, not per point"
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
    stamp = await ship.sky_days(session, other.sky_at)
    coast = ((other.sky_x, other.sky_y), (other.sky_vx, other.sky_vy))
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
    #: The chaser braked to the other's speed with its own engines before it
    #: latched on: the other hull is where its own coast would have taken it,
    #: not pushed off its orbit by somebody else's meeting (D-111).
    world = await sim.system(session, constants)
    at = await ship.sky_days(session, other.sky_at)
    rr, vv = sky.advance(
        world,
        np.array([stamp]),
        np.array([at]),
        np.array([coast[0]]),
        np.array([coast[1]]),
        dt_max=1.0 / (24 * 60),
    )
    #: Measured 2026-09-19: 2e-5 apart by the integrator's own steps, 4e-3
    #: pushed when the chaser latched on without braking.
    assert (other.sky_vx, other.sky_vy) == pytest.approx(tuple(vv[0]), abs=5e-4), "цель не сдвинута"
    assert (other.sky_x, other.sky_y) == pytest.approx(tuple(rr[0]), abs=5e-4)


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


async def test_a_hull_in_orbit_is_met_from_orbit(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A chaser that is not in orbit round the target's planet gets no slider
    and a reason: first a course to the planet, then the meeting (D-354). It
    used to be quoted the straight profile, switched to arcs inside the inner
    sphere with none to fly, and half such approaches went into the ground."""
    chaser, chaser_owner, other, _, since = await _pair(session, constants, catalog)
    world = await sim.system(session, constants)
    terra = world.body(Planet.TERRA.value)
    t = await ship.sky_days(session, since)
    p, vp = sky.place(terra, t)
    #: Three units out -- in sight of the other, outside every orbit that
    #: keeps -- drifting with the planet.
    sim._write_state(
        chaser, (float(p[0, 0]) + 3.0, float(p[0, 1])), (float(vp[0, 0]), float(vp[0, 1])), at=since
    )
    await session.flush()
    assert await sim.orbiting(session, constants, chaser) is None

    forecast = await ship.forecast(session, constants, catalog, chaser, other, now=since)
    assert forecast["samples"] == []
    assert forecast["why"] == {"code": "ship-meet-from-orbit", "args": {"planet": "terra"}}
    with pytest.raises(ship.NoArc) as refused:
        await ship.fly(session, constants, catalog, chaser_owner, chaser, other, hours=5, now=since)
    assert refused.value.key == "ship-meet-from-orbit"
    assert chaser.course is None


@pytest.mark.parametrize("key", [Planet.PYROXIS, Planet.TERRA])
async def test_a_target_in_orbit_is_aimed_at_from_the_moment_of_the_stretch(
    session: AsyncSession, constants: Constants, catalog: Catalog, key: Planet
) -> None:
    """The helm's target in orbit is read by Kepler (`sim.dense_drifter`):
    round Terra from its stamp, which Kepler reads to within a hundredth of
    the meeting distance; round Pyroxis, where it does not, flown under the
    whole sky to the stretch's start first -- read off a stamp hours old it
    jumped at every restamp, and the chaser paid to follow it."""
    home = await _port(session, name="Космодром столицы")
    if key is Planet.PYROXIS:
        await _planet(session, Planet.PYROXIS)
    other, _ = await _hull(session, constants, catalog, home, fuel=FUEL_FOR_TARGET)
    world = await sim.system(session, constants)
    body = world.body(key.value)
    stamp = other.sky_at
    start = await ship.sky_days(session, stamp)
    r, v = sky.parking(world, body, start, 0.0)
    sim._write_state(
        other, (float(r[0, 0]), float(r[0, 1])), (float(v[0, 0]), float(v[0, 1])), at=stamp
    )
    await session.flush()

    later = stamp + timedelta(hours=5)
    t0 = await ship.sky_days(session, later)
    goal = await sim.dense_drifter(session, constants, world, other, t0=t0, t1=t0)
    assert isinstance(goal, sky.Orbiter)
    if key is Planet.PYROXIS:
        assert goal.held.t0 == pytest.approx(t0), "пролетела до начала отрезка"
    else:
        assert goal.held.t0 == pytest.approx(start), "по Кеплеру от своей отметки"
    found = await sim.state_at(session, constants, other, now=later, exact=True)
    assert found is not None
    there = goal.state(t0)[0][0]
    assert (
        float(np.hypot(there[0] - found[0][0], there[1] - found[0][1])) < 0.01 * world.dock_radius
    )


async def test_a_second_hold_moves_whoever_already_holds_on(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hold that moves the reference -- only what the chaser's tanks could
    not pay -- moves the hull already holding on to it too: its own stamp is
    what the sweep would let it go from."""
    first, _, other, _, since = await _pair(session, constants, catalog)
    home = await session.get(Node, first.left_node_id) if first.left_node_id else None
    port = home or await _port(session, name="Второй космодром")
    second, _ = await _hull(session, constants, catalog, port, fuel=FUEL_FOR_TARGET)
    found = await sim.state_at(session, constants, other, now=since, exact=True)
    assert found is not None
    r, v, _ = found
    await hold.begin(session, constants, catalog, first, other, r, v, now=since)
    later = since + timedelta(minutes=1)
    found = await sim.state_at(session, constants, other, now=later, exact=True)
    assert found is not None
    r, v, _ = found
    await hold.begin(session, constants, catalog, second, other, r, (v[0] + 0.4, v[1]), now=later)
    await session.refresh(first)
    assert (first.sky_vx, first.sky_vy) == (other.sky_vx, other.sky_vy), "держащийся летит с парой"
    assert first.sky_at == later
