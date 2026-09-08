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

from typing import NamedTuple

import numpy as np
import pytest

from src import sky
from src.constants import Constants
from src.constants import registry as R
from src.models.world import Planet
from src.sky import _base, field, forecast, guide, plan
from src.units import HOURS_PER_DAY, MINUTES_PER_HOUR, TRACE_POINTS


class Circle(NamedTuple):
    """One world's orbit as the sky reads it: where, how long, and from where."""

    key: str
    radius: float
    period_days: float
    phase: float


#: The seed's system with the vault's own numbers, written out by hand: the
#: arithmetic is tested against the vault's shape, not against its build, and
#: a build read here would make every one of these tests a test of the build.
#: The two numbers a world has are shares of the **Earth's** (D-320, D-324) --
#: mass, which the sky turns into a pull, and radius, which the map's scale
#: turns into ground; the land one walks is neither and is not the sky's.
#:
#: **Kept equal to the vault by a test of their own** (below). They were not,
#: and that is how a world where Pyroxis weighs three hundred Earths was flown
#: here as one where it weighs one and a third: every test in this file passed
#: against a system that no longer existed, the parking circle's among them.
MASS = {"terra": 1.0, "pyroxis": 317.8, "aurora": 1.5, "aquatica": 0.5}
SHARE = {"terra": 1.0, "pyroxis": 10.973, "aurora": 1.393, "aquatica": 0.838}
PLANET_MU = 24.0
BODY_RADIUS = 0.08

#: Where each world circles, written out for the same reason the masses are:
#: this file tests the arithmetic against the vault's **shape**, and a build
#: read here would make every test a test of the build. The radius is not a
#: number the vault keeps -- it follows the year by Kepler (`sky.circle_of`) --
#: and these four triples are checked against it below.
ORBITS = (
    Circle("pyroxis", 72.95, 11.0, 0.80),
    Circle("terra", 136.0, 28.0, 2.10),
    Circle("aquatica", 250.51, 70.0, 4.00),
    Circle("aurora", 378.50, 130.0, 2.28),
)


def _system(*, bodies: bool = True) -> sky.System:
    #: The vault's own layout since 2026-09-08 (`sky.circle_of`): the year is
    #: the tuned number and the radius follows it by Kepler.
    circles = {one.key: (one.radius, one.period_days, one.phase) for one in ORBITS}
    mu = _base.astro.mu_of(circles["terra"])
    return sky.System(
        mu=mu,
        bodies=tuple(
            sky.Body(
                key=key, orbit=orbit, mu=PLANET_MU * MASS[key], radius=BODY_RADIUS * SHARE[key]
            )
            for key, orbit in sorted(circles.items())
        )
        if bodies
        else (),
        corona=35.0,
        edge=800.0,
        #: Three of each body's own radii (D-324). Terra's is `BODY_RADIUS`
        #: times a share of one, so its circle is the same 1.5 units these
        #: tests were written against.
        park_radii=3.0,
        #: Twelve of each body's own radii: four times the circle, which is
        #: the room the weakest legal hull needs to brake into it (D-324).
        capture_radii=12.0,
        capture_speed=2.0,
        eject_window=0.15,
        approach=4.0,
        late_leg=0.25,
        dock_radius=0.2,
        dock_speed=0.5,
        sight_radius=5.0,
    )


def test_a_world_is_built_from_the_two_numbers_the_vault_gives_it() -> None:
    """`system_of` is where the shares become a body (D-320).

    The mass share becomes the pull on the vault's scale, and the radius share
    becomes ground on the map's -- and the map's scale is a number of its own
    (`orbit.body_radius`), because the bodies are drawn far larger than life on
    purpose. Drop the scale and every world doubles; nothing else in this file
    would notice.
    """
    constants = Constants(
        {
            R.PLANET_MASS.key: MASS,
            R.PLANET_RADIUS.key: SHARE,
            R.ORBIT_PLANET_MU.key: PLANET_MU,
            R.ORBIT_BODY_RADIUS.key: BODY_RADIUS,
            R.ORBIT_CORONA_RADIUS.key: 35.0,
            R.ORBIT_SYSTEM_RADIUS.key: 800.0,
            #: Three of the body's own radii -- with `BODY_RADIUS` at a half
            #: and Terra's share at one, the same 1.5 units these tests were
            #: written against (D-324).
            R.ORBIT_PARK_RADII.key: 3.0,
            R.ORBIT_CAPTURE_RADII.key: 12.0,
            R.ORBIT_CAPTURE_SPEED.key: 2.0,
            R.ORBIT_EJECT_WINDOW.key: 0.15,
            R.ORBIT_APPROACH_RADII.key: 4.0,
            R.ORBIT_LATE_LEG_DAYS.key: 0.25,
            R.ORBIT_DOCK_RADIUS.key: 0.2,
            R.ORBIT_DOCK_SPEED.key: 0.5,
            R.ORBIT_SIGHT_RADIUS.key: 5.0,
        },
        source="тест",
    )
    orbits = {
        Planet(one.key): (float(one.radius), float(one.period_days), float(one.phase))
        for one in ORBITS
    }
    world = sky.system_of(constants, orbits)
    for body in world.bodies:
        assert body.mu == pytest.approx(PLANET_MU * MASS[body.key])
        assert body.radius == pytest.approx(BODY_RADIUS * SHARE[body.key])
    #: And the ground stays well inside the circle a hull moors on, or mooring
    #: would be landing (D-289). Asked of each world in turn since D-324: the
    #: circle is so many radii of its **own** body, and the bodies are no
    #: longer one size -- one flat number of units compared against the
    #: largest of them was the check that let Pyroxis' circle sink into it.
    for one in world.bodies:
        assert one.radius < sky.park_of(world, one)


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
    park = sky.park_of(system, terra)
    lap = 2 * np.pi / sky.circle_rate(terra, park)
    r0, v0 = sky.parking(system, terra, 0.0, 0.3)
    r, _ = field.advance(system, np.array([0.0]), np.array([lap]), r0, v0, dt_max=0.05)
    p0, _ = _base.place(terra, 0.0)
    p1, _ = _base.place(terra, lap)
    before = r0 - p0
    after = r - p1
    assert float(np.hypot(*(after - before)[0])) < 0.05 * park


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
        system, 0.0, here, tuple(outward * speed * 3), horizon=15.0, dt_max=1 / 24
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
    #: Just outside the window the mooring watches, said as a share of it
    #: rather than in units: the window is so many radii of the world (D-324),
    #: and a hull placed at a flat 3.5 units was a hair outside it while the
    #: circles were Terra-sized and seven windows away once they were not.
    r = (float(p[0, 0]) + sky.capture_of(system, terra) * 1.2, float(p[0, 1]))
    #: Across the way it is going, at about a tenth of the circle's speed:
    #: a hull that has come to the planet, not one falling straight in.
    circle = sky.circle_speed(terra, sky.park_of(system, terra))
    v = (float(vp[0, 0]), float(vp[0, 1]) + circle / 10)
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


def test_the_quote_to_a_hull_is_what_the_approach_profile_flies() -> None:
    """One price to a hull (D-289, wave 3): the hours and the delta-v of a
    run-up at full thrust and a run-down at the profile's share, plus the
    speed the two differ by -- and the profile the helm actually flies
    (`guide._meet`) comes to rest inside them."""
    system = _system(bodies=False)
    a_max = 40.0
    still = _base.Drifter(key="hull", t0=0.0, t1=10.0, trace=((0.0, 0.0), (0.0, 0.0)))
    gap = 3.0
    quote = sky.approach_quote((gap, 0.0), (0.0, 0.0), 0.0, still, a_max)
    push = guide.BRAKE_SHARE * a_max
    peak = np.sqrt(2.0 * gap / (1.0 / a_max + 1.0 / push))
    assert quote.dv == pytest.approx(2 * peak) and quote.dv_in == 0.0
    assert quote.hours == pytest.approx((peak / a_max + peak / push) * HOURS_PER_DAY)
    assert quote.trace[0] == (gap, 0.0) and quote.trace[-1] == (0.0, 0.0)
    #: Coming in at a speed: shedding it is paid for, in delta-v and in time.
    moving = sky.approach_quote((gap, 0.0), (0.0, 2.0), 0.0, still, a_max)
    assert moving.dv == pytest.approx(quote.dv + 2.0)
    assert moving.hours > quote.hours
    #: A hull already alongside is quoted a minute, not nothing.
    assert sky.approach_quote((0.0, 0.0), (0.0, 0.0), 0.0, still, a_max).hours == plan.LEAST_HOURS

    #: The profile flown step by step, no sky pulling, comes to rest in the
    #: hold's radius no later than the quote says.
    dt = 1.0 / HOURS_PER_DAY / MINUTES_PER_HOUR
    rel, v_rel = np.array([gap, 0.0]), np.array([0.0, 0.0])
    days = 0.0
    while days < 2 * quote.hours / HOURS_PER_DAY:
        #: A system without bodies: nothing holds the chaser, so the
        #: departure rule (D-316) has nothing to take off the profile.
        helm = guide._meet(
            system,
            still,
            0.0,
            (float(rel[0]), float(rel[1])),
            (float(v_rel[0]), float(v_rel[1])),
            rel,
            v_rel,
            a_max=a_max,
            dt=dt,
        )
        if helm.captured:
            break
        v_rel = v_rel + np.array(helm.thrust) * dt
        rel = rel + v_rel * dt
        days += dt
    assert helm.captured, "профиль доводит до удержания"
    assert days * HOURS_PER_DAY <= quote.hours * 1.05


def test_the_circle_round_the_star_is_matched_and_priced() -> None:
    """The astrocentric orbit (D-289, 2026-09-04): the circle's velocity is
    across the radius at the circle's speed, prograde; the helm burns toward
    it at full thrust and is done within the capture speed; the quote is that
    difference and the hours to burn it off."""
    system = _system(bodies=False)
    r = (10.0, 0.0)
    wanted = sky.star_circle(system, r)
    assert wanted[0] == pytest.approx(0.0) and wanted[1] == pytest.approx(
        np.sqrt(system.mu / 10.0)
    ), "prograde, across the radius"
    still = guide._circle(system, r, (0.0, 0.0), a_max=4.0, dt=1.0 / HOURS_PER_DAY)
    assert not still.captured and float(np.hypot(*still.thrust)) == pytest.approx(4.0), (
        "full thrust toward the circle"
    )
    assert still.thrust[1] > 0 and still.thrust[0] == pytest.approx(0.0)
    near = guide._circle(
        system, r, (0.0, float(wanted[1]) - system.capture_speed / 2), a_max=4.0, dt=1.0
    )
    assert near.captured, "within the capture speed the order is done"
    quote = sky.circle_quote(system, r, (0.0, 0.0), 4.0)
    assert quote.dv == pytest.approx(float(wanted[1])) and quote.dv_in == 0.0
    assert quote.hours == pytest.approx(float(wanted[1]) / 4.0 * HOURS_PER_DAY)
    assert quote.trace == (r, r), "no line: the burn goes nowhere"


def _flown(
    system: sky.System, home: sky.Body, goal: sky.Body, *, ratio: float, heading: float
) -> tuple[float, bool]:
    """Fly a real point of the slider from `home`'s parking circle to `goal`
    and say where it ended relative to `goal` and whether it moored.

    The whole passage on the sky's own terms -- the planner's arc, the helm's
    hand, the five bodies' pull, the tick's minute -- with no database in it.
    """
    scale, share = 5400.0, 0.5
    _, vp = _base.place(home, 0.0)
    phase = float(np.arctan2(float(vp[0, 1]), float(vp[0, 0]))) + heading
    r0, v0 = _base.parking(system, home, 0.0, phase)
    r = (float(r0[0, 0]), float(r0[0, 1]))
    v = (float(v0[0, 0]), float(v0[0, 1]))
    grid = tuple(2.0 * 1.25**step for step in range(24))
    offered = plan.preview(
        system,
        None,  # type: ignore[arg-type]
        r,
        v,
        0.0,
        goal,
        grid,
        leaving=home,
    )
    #: The cheapest point the engines can still deliver: the middle of the
    #: slider, where an arrival is fast enough to be hard and slow enough to
    #: be offered.
    can = [one for one in offered if one.dv <= ratio * scale * one.hours / HOURS_PER_DAY * share]
    assert can, "небо предлагает хоть одну дугу этому кораблю"
    pick = can[len(can) // 2]
    a_max = ratio * scale
    arrive = (pick.wait + pick.hours) / HOURS_PER_DAY
    dt = 1.0 / MINUTES_PER_HOUR / HOURS_PER_DAY
    t = 0.0
    while t < arrive * 4 + 5.0:
        helm = guide.steer(system, goal, t, r, v, arrive=arrive, a_max=a_max, dt=dt)
        rel = np.array(r) - _base.place(goal, t)[0][0]
        if helm.captured:
            return float(np.hypot(*rel)), True
        rr, vv = field.advance(
            system,
            np.array([t]),
            np.array([t + dt]),
            np.array([r]),
            np.array([v]),
            dt_max=dt,
            thrust=np.array(helm.thrust)[None, :],
        )
        r = (float(rr[0, 0]), float(rr[0, 1]))
        v = (float(vv[0, 0]), float(vv[0, 1]))
        t += dt
    return float(np.hypot(*(np.array(r) - _base.place(goal, t)[0][0]))), False


def test_the_arrival_stops_on_the_circle_however_weak_the_hull() -> None:
    """The capture falls to the parking circle and brakes there -- and brakes
    early enough that a hull of the least legal thrust still stops on it.

    The mooring is measured against the circle's own speed at
    `orbit.park_radii`, so a hull that sails past and settles on whatever
    ring it reaches is never recognised as arrived: the order would stand for
    ever. Letting the fall run merely while it clears the ground is not
    enough -- the ground is a third of the circle's radius, and a hull near
    `ship.min_thrust_ratio` cannot stop in what is left of the fall. So the
    fall runs only while it is longer than the braking, which for a strong
    hull is very nearly the whole way down and for a weak one is not.
    """
    system = _system()
    for src, dst in (("terra", "pyroxis"), ("terra", "aurora")):
        for ratio in (0.15, 0.5):
            where, moored = _flown(
                system, system.body(src), system.body(dst), ratio=ratio, heading=1.05
            )
            assert moored, f"{src}->{dst} при тяговооружённости {ratio}: приказ закрылся"
            #: Not below the circle: deeper than it the mooring's own measure
            #: -- the circle's speed at `park` -- stops matching, and the hull
            #: circles for ever. Anywhere inside the capture radius above it
            #: is the arrival as D-289 defines it.
            park = sky.park_of(system, system.body(dst))
            assert park - 0.05 <= where <= sky.capture_of(system, system.body(dst)), (
                f"{src}->{dst} при тяговооружённости {ratio}: борт не провалился под круг"
            )


def test_the_numbers_here_are_the_vaults_own(constants: Constants) -> None:
    """The hand-written system above is the vault's, and stays it.

    Every other test in this file flies that system. When it and the vault
    part company the whole file quietly becomes a test of a world nobody
    lives in -- which is exactly what happened to D-324: the masses changed,
    the fixture did not, and the parking circle went on coming back after a
    lap in a system where Pyroxis weighed one and a third Earths.

    The build is read **here and nowhere else** in this file, so the
    arithmetic stays tested against the vault's shape rather than against its
    numbers, and the numbers are checked once, out loud.
    """
    assert {key: float(value) for key, value in constants[R.PLANET_MASS].items()} == MASS
    assert {key: float(value) for key, value in constants[R.PLANET_RADIUS].items()} == SHARE
    assert float(constants[R.ORBIT_PLANET_MU]) == PLANET_MU
    assert float(constants[R.ORBIT_BODY_RADIUS]) == BODY_RADIUS
    assert _system().park_radii == float(constants[R.ORBIT_PARK_RADII])
    assert _system().capture_radii == float(constants[R.ORBIT_CAPTURE_RADII])
    #: And where the worlds circle, radius included -- which is the one number
    #: here that the vault does not keep: it follows the year by Kepler, and a
    #: triple written out by hand is exactly where that law gets broken.
    for one in ORBITS:
        radius, period, phase = sky.circle_of(constants, one.key)
        #: The radius to a hundredth, as it is written above: the literal is a
        #: reader's number, and Kepler's own has fifteen digits after it.
        assert one.radius == pytest.approx(radius, rel=1e-4), one.key
        assert (one.period_days, one.phase) == (period, phase), one.key
