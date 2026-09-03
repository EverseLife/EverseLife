# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The sky, simulated (D-289): the arithmetic alone, no rows.

Pinned is what the simulation stands on:

* the integrator keeps a circle a circle over a year, and a parking circle
  round a planet comes back where it started after a lap;
* the slider's preview ends every arc where the planet will be, and the
  order flies under all five bodies -- the helm re-solving each step -- to within the
  tolerance;
* the forecast tells a coast into the star from a coast out of the system
  from a coast that stays;
* the helm captures a hull that arrives near its planet, and coasts when
  the arc it is on already arrives.
"""

from __future__ import annotations

import numpy as np
import pytest

from src import seed_parts, sky
from src.sky import _base, field, forecast, guide
from src.units import HOURS_PER_DAY, TRACE_POINTS

#: The seed's system with D-289's starting numbers, built by hand: the
#: arithmetic is tested against the vault's shape, not the vault's build.
GRAVITY = {"terra": 1.0, "pyroxis": 1.3, "aurora": 0.8, "aquatica": 1.1}
RADIUS = {"terra": 0.5, "pyroxis": 0.35, "aurora": 0.45, "aquatica": 0.6}
PLANET_MU = 150.0


def _system(*, bodies: bool = True) -> sky.System:
    circles = {one.key: (one.radius, one.period_days, one.phase) for one in seed_parts.SYSTEM}
    mu = _base.astro.mu_of(circles["terra"])
    return sky.System(
        mu=mu,
        bodies=tuple(
            sky.Body(key=key, orbit=orbit, mu=PLANET_MU * GRAVITY[key], radius=RADIUS[key])
            for key, orbit in sorted(circles.items())
        )
        if bodies
        else (),
        corona=35.0,
        edge=800.0,
        park=1.5,
        capture_radius=3.0,
        capture_speed=2.0,
        approach=4.0,
        late_leg=0.25,
        dock_radius=0.2,
        dock_speed=0.5,
        sight_radius=5.0,
    )


def test_the_integrator_keeps_a_circle_a_circle() -> None:
    """Runge-Kutta with the step bounded by the orbital time scale: a year on a
    circle round the star alone changes the radius by less than a thousandth."""
    system = _system(bodies=False)
    terra = _system().body("terra")
    r0, v0 = _base.place(terra, 0.0)
    r, v = field.advance(
        system, np.array([0.0]), np.array([365.0]), r0, v0, dt_max=HOURS_PER_DAY / 24
    )
    assert abs(float(_base.norms(r)[0]) - terra.orbit[0]) / terra.orbit[0] < 1e-3
    energy_before = 0.5 * float(np.sum(v0 * v0)) - system.mu / terra.orbit[0]
    energy_after = 0.5 * float(np.sum(v * v)) - system.mu / float(_base.norms(r)[0])
    assert abs(energy_after - energy_before) / abs(energy_before) < 1e-4


def test_a_parking_circle_comes_back_after_a_lap() -> None:
    """The hull on the parking circle is flown under all five bodies for one
    lap and ends where it began **relative to its planet** -- the star's pull
    on both cancels, which is what makes the circle analytic (D-289)."""
    system = _system()
    terra = system.body("terra")
    lap = 2 * np.pi / sky.circle_rate(terra, system.park)
    r0, v0 = sky.parking(system, terra, 0.0, 0.3)
    r, _ = field.advance(system, np.array([0.0]), np.array([lap]), r0, v0, dt_max=0.05)
    p0, _ = _base.place(terra, 0.0)
    p1, _ = _base.place(terra, lap)
    before = r0 - p0
    after = r - p1
    assert float(np.hypot(*(after - before)[0])) < 0.05 * system.park


def test_the_preview_ends_every_arc_where_the_planet_will_be() -> None:
    """Two-body arcs, priced at both ends: the trace's last point is the
    planet's place at arrival, and leaving costs more than the excess alone."""
    system = _system()
    terra, pyroxis = system.body("terra"), system.body("pyroxis")
    t0 = 3.0
    r0, v0 = sky.parking(system, terra, t0, 0.0)
    hours = (48.0, 96.0, 240.0)
    samples = sky.preview(
        system,
        None,  # type: ignore[arg-type]
        (float(r0[0, 0]), float(r0[0, 1])),
        (float(v0[0, 0]), float(v0[0, 1])),
        t0,
        pyroxis,
        hours,
        leaving=terra,
    )
    assert [one.hours for one in samples] == list(hours)
    for one in samples:
        goal = _base.place(pyroxis, t0 + one.hours / HOURS_PER_DAY)[0][0]
        end = one.trace[-1]
        assert np.hypot(end[0] - goal[0], end[1] - goal[1]) < 1e-3
        assert len(one.trace) == TRACE_POINTS
        assert one.dv_out > 0 and one.dv_in > 0
        assert one.dv == pytest.approx(one.dv_out + one.dv_in)
    #: Faster is dearer, on the fast side of the slider.
    assert samples[0].dv > samples[-1].dv


def test_the_forecast_names_the_end_of_a_coast() -> None:
    """Into the star, out of the system, or round for ever -- with the hour."""
    system = _system()
    terra = system.body("terra")
    r0, v0 = _base.place(terra, 0.0)
    here = (float(r0[0, 0]) + 5.0, float(r0[0, 1]))
    speed = float(_base.norms(v0)[0])
    #: Straight in and straight out, along the line to the star.
    outward = np.array(here) / np.hypot(*here)

    falling = sky.inertia(system, 0.0, here, tuple(-outward * speed), horizon=90.0, dt_max=1 / 24)
    assert falling.kind == forecast.CRASH and falling.body == "star"
    assert 0 < falling.at < 90

    fleeing = sky.inertia(
        system, 0.0, here, tuple(outward * speed * 3), horizon=90.0, dt_max=1 / 24
    )
    assert fleeing.kind == forecast.ESCAPE and fleeing.at < 90

    #: On Terra's circle but across the star from it: the same speed five
    #: units off the planet is a fall onto the planet, not a lap round the
    #: star -- the planets pull (D-289), and that is the point of them.
    far = (-float(r0[0, 0]), -float(r0[0, 1]))
    staying = sky.inertia(
        system, 0.0, far, (-float(v0[0, 0]), -float(v0[0, 1])), horizon=90.0, dt_max=1 / 24
    )
    assert staying.kind == forecast.STABLE and staying.at == pytest.approx(90.0)
    assert len(staying.trace) == TRACE_POINTS


def test_the_helm_captures_a_hull_that_arrives_near_its_planet() -> None:
    """Near the planet the helm matches the circle and puts the hull on it."""
    system = _system()
    terra = system.body("terra")
    t = 5.0
    p, vp = _base.place(terra, t)
    r = (float(p[0, 0]) + 3.5, float(p[0, 1]))
    v = (float(vp[0, 0]), float(vp[0, 1]) + 1.0)
    dt = 1 / 24 / 60
    captured = False
    for _ in range(24 * 60):
        helm = sky.steer(system, terra, t, r, v, arrive=t + 1, a_max=200.0, dt=dt)
        if helm.captured:
            captured = True
            break
        rr, vv = field.advance(
            system,
            np.array([t]),
            np.array([t + dt]),
            np.array([r]),
            np.array([v]),
            dt_max=dt,
            thrust=np.array([helm.thrust]),
        )
        r, v, t = (float(rr[0, 0]), float(rr[0, 1])), (float(vv[0, 0]), float(vv[0, 1])), t + dt
    assert captured, "за сутки у планеты автопилот не поставил корпус на круг"


def test_the_helm_coasts_on_an_arc_that_already_arrives() -> None:
    """Flying the Lambert velocity itself, there is nothing to burn."""
    system = _system(bodies=False)
    terra, pyroxis = _system().body("terra"), _system().body("pyroxis")
    t0, tof = 2.0, 6.0
    r0 = _base.place(terra, t0)[0][0]
    goal = _base.place(pyroxis, t0 + tof)[0][0]
    v1 = guide._lambert_velocity(
        system.mu, (float(r0[0]), float(r0[1])), (float(goal[0]), float(goal[1])), tof, (0.0, 0.0)
    )
    assert v1 is not None
    helm = sky.steer(
        system,
        pyroxis,
        t0,
        (float(r0[0]), float(r0[1])),
        v1,
        arrive=t0 + tof,
        #: A hull's thrust, not a toy's: at ten units a day squared the way
        #: braking needs is the whole passage, and the helm would be right
        #: to start shedding speed at once.
        a_max=300.0,
        dt=1 / 24,
    )
    assert helm.phase == guide.COAST and helm.thrust == (0.0, 0.0)
