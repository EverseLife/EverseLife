# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A meeting in orbit, in the arithmetic alone (D-354, wave 3).

Pinned:

* a hull in orbit, as a target, is its orbit read by Kepler -- not chords
  across it;
* only a hull in orbit is met on an arc round its planet, and only by a hull
  close in round the same planet; any other target is the straight profile's;
* two hulls on one circle half a lap apart meet on an arc round the planet,
  and the helm flies it for the price it was quoted, clear of the ground --
  the straight profile drove through the planet;
* a hull under way is corrected, not sent onto another arc: a small drift
  costs a small burn;
* Lambert's two arcs of a lap count, near the least time that count is
  flown in, are both found -- a hull flying one was told for a minute that
  it did not exist;
* an arc whose burns are no instants for the engines, or which the tide
  makes a fiction of, is not offered.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from sky_kit import system as _system
from src import astro, sky
from src.sky import _base, rendezvous

#: A thrust the tests fly at, units a day squared: a middling hull's.
THRUST = 2700.0
#: The helm's step, days: a minute, as the tick flies (`orbit.step_minutes`).
STEP = 1.0 / (24 * 60)
#: A moment of the sky the tests stand at, sky days.
T0 = 3.0


def _circle(world: sky.System, key: str, phase: float) -> tuple[tuple, tuple]:
    """A hull on `key`'s parking circle, `phase` radians round."""
    r, v = sky.parking(world, world.body(key), T0, phase)
    return (float(r[0, 0]), float(r[0, 1])), (float(v[0, 0]), float(v[0, 1]))


def _orbiter(world: sky.System, key: str, phase: float) -> sky.Orbiter:
    r, v = _circle(world, key, phase)
    held = sky.bound_to(world, T0, r, v)
    assert held is not None
    return sky.orbiter("other", held)


def _fly(world, target, r, v, *, arrive: float, a_max: float) -> tuple[bool, float, float, float]:
    """The helm flown a minute at a time under the whole sky until the hold:
    whether it came, the speed it burnt, the hour, and how near Terra's
    centre it passed, in Terra's radii."""
    terra = world.body("terra")
    t, spent, low = T0, 0.0, math.inf
    while t < arrive + 0.25:
        helm = sky.steer(world, target, t, r, v, arrive=arrive, a_max=a_max, dt=STEP)
        if helm.captured:
            return True, spent, t, low
        thrust = np.array(helm.thrust)
        spent += float(np.hypot(*thrust)) * STEP
        rr, vv = sky.advance(
            world,
            np.array([t]),
            np.array([t + STEP]),
            np.array([r]),
            np.array([v]),
            dt_max=STEP,
            thrust=thrust[None, :],
        )
        r, v = (float(rr[0, 0]), float(rr[0, 1])), (float(vv[0, 0]), float(vv[0, 1]))
        t += STEP
        p, _ = sky.place(terra, t)
        low = min(low, math.hypot(r[0] - p[0, 0], r[1] - p[0, 1]) / terra.radius)
    return False, spent, t, low


def test_a_hull_in_orbit_as_a_target_is_read_by_kepler() -> None:
    world = _system()
    other = _orbiter(world, "terra", 1.0)
    terra = world.body("terra")
    held = other.held
    for hours in (0.5, 2.0, 9.0, 40.0):
        t = T0 + hours / 24
        rows, speeds = other.state(t)
        rel, v_rel = astro.propagate(terra.mu, held.rel, held.v_rel, hours / 24)
        p, vp = sky.place(terra, t)
        assert rows[0] == pytest.approx(np.array(rel) + p[0], abs=1e-9)
        assert speeds[0] == pytest.approx(np.array(v_rel) + vp[0], abs=1e-7)
    assert other.loops, "виток без конца"


def test_only_a_hull_in_orbit_is_met_round_a_planet() -> None:
    world = _system()
    here, _ = _circle(world, "terra", 0.0)
    other = _orbiter(world, "terra", math.pi)
    assert rendezvous.shared_world(world, T0, here, other) is world.body("terra")
    #: The same place as a line of points -- a hull on no orbit that keeps.
    line = sky.Drifter(key="line", t0=T0, t1=T0 + 1, trace=(other.state(T0)[0][0].tolist(),) * 2)
    assert rendezvous.shared_world(world, T0, here, line) is None
    #: And the hull out in the deep, far from Terra's inner sphere.
    p, _ = sky.place(world.body("terra"), T0)
    far = (float(p[0, 0]) + 10.0, float(p[0, 1]))
    assert rendezvous.shared_world(world, T0, far, other) is None


def test_half_a_lap_behind_is_met_on_an_arc_round_the_planet() -> None:
    """The straight profile aimed through Terra; the arc goes round it, and
    the helm flies it for what it was quoted -- a tenth either way."""
    world = _system()
    r, v = _circle(world, "terra", 0.0)
    other = _orbiter(world, "terra", math.pi)
    home = rendezvous.shared_world(world, T0, r, other)
    assert home is not None
    grid = [2.0 * 1.1**k for k in range(30)]
    laid = sky.meet_quotes(world, home, T0, r, v, other, grid, THRUST)
    offered = sky.choices(laid, reach=THRUST, gap=4.0)
    assert len(offered) > 3, "ползунок, а не одна цена"
    assert all(one.around == "terra" for one in offered)
    assert [one.dv for one in offered] == sorted((one.dv for one in offered), reverse=True)
    quote = next(one for one in offered if one.hours >= 5.0)
    came, spent, t, low = _fly(world, other, r, v, arrive=T0 + quote.hours / 24, a_max=THRUST)
    assert came, "встретились"
    assert spent == pytest.approx(quote.dv, rel=0.1)
    assert (t - T0) * 24 == pytest.approx(quote.hours, abs=0.25)
    assert low > _base.GROUND_MARGIN * 0.9, "мимо земли"


def test_a_hull_under_way_is_corrected_not_sent_onto_another_arc() -> None:
    """Forty laps into an eight-day meeting a drift of a thousandth of a unit
    a day is a burn of that size, not a new arc of another lap count."""
    world = _system()
    terra = world.body("terra")
    r, v = _circle(world, "terra", 0.0)
    other = _orbiter(world, "terra", math.pi)
    tof = 8.0
    arc = rendezvous.arc_to(world, terra, T0, r, v, other, tof)
    assert arc is not None and arc.revs > 30
    #: Along the way it goes: a change of the lap's length, which a coast of
    #: fifty laps turns into a miss.
    p, vp = sky.place(terra, T0)
    along = np.array(arc.v1) - vp[0]
    along /= float(np.hypot(*along))
    drifted = (arc.v1[0] + 1e-3 * float(along[0]), arc.v1[1] + 1e-3 * float(along[1]))
    miss, _ = rendezvous.coast_end(terra, T0, r, drifted, other, tof)
    assert miss > rendezvous.AIM_SHARE * world.dock_radius, "дрейф заметен"
    fix = rendezvous.corrected(world, terra, T0, r, drifted, other, tof)
    assert fix is not None and fix.dv_out < 5e-3
    assert rendezvous.coast_end(terra, T0, r, fix.v1, other, tof)[0] < miss / 2


def test_both_arcs_of_a_lap_count_near_its_least_time_are_found() -> None:
    mu, r1 = 1.0, (1.0, 0.0)
    r2 = (math.cos(2.0), math.sin(2.0))
    least = min(
        (tof for tof in np.linspace(6.0, 14.0, 4001) if astro.lambert(mu, r1, r2, tof, 1)),
        default=None,
    )
    assert least is not None
    for above in (1e-2, 1e-3, 1e-4):
        found = astro.lambert(mu, r1, r2, least * (1 + above), 1)
        assert len(found) == 2, f"{above}: {len(found)}"


def test_an_arc_that_is_no_instant_or_no_arc_is_not_offered() -> None:
    world = _system()
    terra = world.body("terra")
    r, v = _circle(world, "terra", 0.0)
    other = _orbiter(world, "terra", math.pi)
    fastest = rendezvous.arc_to(world, terra, T0, r, v, other, 2.3 / 24)
    assert fastest is not None and fastest.dv_out > 10.0
    assert not rendezvous.flown_as_priced(world, terra, fastest, 2.3 / 24, 810.0)
    assert rendezvous.flown_as_priced(world, terra, fastest, 2.3 / 24, 1e6)
    #: The tide: round Pyroxis a meeting of days is a fiction, round Terra not.
    grid = [2.0 * 1.1**k for k in range(52)]
    for key, longest in (("pyroxis", 20.0), ("terra", 200.0)):
        here, speed = _circle(world, key, 0.0)
        target = _orbiter(world, key, math.pi)
        hours = [
            one.hours
            for one in sky.meet_quotes(world, world.body(key), T0, here, speed, target, grid, 1e6)
        ]
        assert hours, key
        if key == "pyroxis":
            assert max(hours) < longest, key
        else:
            assert max(hours) > longest, key


def test_the_same_ray_is_coasted() -> None:
    """Both places on one ray from the planet: the arc's plane is undefined,
    and the helm coasts that minute rather than take Lambert's word."""
    world = _system()
    terra = world.body("terra")
    r, v = _circle(world, "terra", 0.0)
    other = _orbiter(world, "terra", 0.0)
    lap = (
        2 * math.pi * sky.park_of(world, terra) / sky.circle_speed(terra, sky.park_of(world, terra))
    )
    assert rendezvous.arc_to(world, terra, T0, r, v, other, 3 * lap) is None
