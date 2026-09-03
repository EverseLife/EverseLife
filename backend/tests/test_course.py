# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The sky's arithmetic (D-271): Kepler, Lambert and the slider.

Pure functions on orbits and constants -- no world, no rows. What is pinned
here is the physics the passages rest on: the Hohmann transfer falls out of
the Lambert solver at the window, a fast arc is nearly straight and costs
by its speed, the corona is not cut through, the planet a hull bends round
turns it for free up to its own limit, and the hull's thrust bounds the fast
end of the slider.
"""

from __future__ import annotations

import math

import pytest

from src.constants import Constants
from src.constants import registry as R
from src.engine.ship import course
from src.units import TRACE_POINTS

#: A textbook pair: the inner planet laps the outer four times a year.
INNER: course.Orbit = (100.0, 10.0, 0.0)
OUTER: course.Orbit = (100.0 * 4 ** (2 / 3), 40.0, 1.0)


def _hohmann(mu: float, r1: float, r2: float) -> tuple[float, float]:
    """Half the period of the touching ellipse, and the two burns' sum."""
    a = (r1 + r2) / 2
    days = math.pi * math.sqrt(a**3 / mu)
    leave = abs(math.sqrt(mu / r1) * (math.sqrt(2 * r2 / (r1 + r2)) - 1))
    match = abs(math.sqrt(mu / r2) * (1 - math.sqrt(2 * r1 / (r1 + r2))))
    return days, leave + match


def test_the_star_is_read_off_any_orbit() -> None:
    """Kepler III: `r^3 / T^2` is the star, whichever planet says it."""
    assert course.mu_of(INNER) == pytest.approx(course.mu_of(OUTER))
    #: Circular motion at the speed the period says.
    (x, y), (vx, vy) = course.place(INNER, 2.5)
    assert math.hypot(x, y) == pytest.approx(100)
    assert math.hypot(vx, vy) == pytest.approx(math.tau * 100 / 10)
    #: A quarter of a period on: a quarter turn, at right angles.
    assert (x, y) == pytest.approx((0, 100), abs=1e-9)


def test_the_cheapest_arc_at_the_window_is_the_hohmann_transfer(constants: Constants) -> None:
    """Lambert against the closed form: the minimum over departure days of the
    half-ellipse's delta-v is the Hohmann transfer's, to three figures."""
    mu = course.mu_of(INNER)
    days, dv = _hohmann(mu, INNER[0], OUTER[0])
    best = min(
        arc.dv
        for day in [i / 20 for i in range(0, 20 * 14)]
        if (arc := course.arc(constants, INNER, OUTER, day, days * 24)) is not None
    )
    assert best == pytest.approx(dv, rel=1e-3)


def test_a_fast_arc_costs_by_its_speed_and_ends_on_the_planet(constants: Constants) -> None:
    """A short flight is nearly a straight line flown at distance over time,
    and its trace ends exactly where the target planet will be."""
    tof_hours = 6.0
    arc = course.arc(constants, INNER, OUTER, 3.0, tof_hours)
    assert arc is not None
    start = course.place(INNER, 3.0)[0]
    end = course.place(OUTER, 3.0 + tof_hours / 24)[0]
    gap = math.dist(start, end)
    #: Twice the straight-line speed, give or take the planets' own motion:
    #: one burn to get going, one to stop.
    assert arc.dv == pytest.approx(2 * gap / (tof_hours / 24), rel=0.35)
    assert arc.trace[0] == pytest.approx(start)
    assert arc.trace[-1] == pytest.approx(end, abs=1e-3)
    assert len(arc.trace) == TRACE_POINTS


def test_the_corona_is_not_cut_through(constants: Constants) -> None:
    """With the target straight across the star, a fast arc through it is not
    offered; a slow one round it still is."""
    #: The outer planet half a turn away at the moment of arrival, for a
    #: flight of a few hours: the straight line passes through the star.
    across: course.Orbit = (OUTER[0], OUTER[1], math.pi)
    fast = course.arc(constants, INNER, across, 0.0, 6.0)
    assert fast is None, "сквозь корону не срезают"
    slow = course.arc(constants, INNER, across, 0.0, 15 * 24)
    assert slow is not None and slow.perihelion >= constants[R.ORBIT_CORONA_RADIUS]


def test_the_slider_runs_from_the_engines_to_the_horizon(constants: Constants) -> None:
    """Samples cover the grid from hours to the vault's horizon; the fast end
    is bounded by what the engines deliver, the cheap end is the minimum."""
    samples = course.curve(constants, INNER, OUTER, 3.0)
    assert samples[0].hours == constants[R.ORBIT_SLIDER_FROM_HOURS]
    assert samples[-1].hours == constants[R.ORBIT_LONGEST_DAYS] * 24
    cheap = course.cheapest(samples)
    assert cheap is not None and cheap.dv == min(s.dv for s in samples)
    strong = course.fastest(constants, samples, 2.0)
    weak = course.fastest(constants, samples, 0.2)
    assert strong is not None and weak is not None
    assert strong.hours < weak.hours, "сильные двигатели открывают быстрый край"
    assert strong.dv <= course.deliverable(constants, 2.0, strong.hours)
    #: The memo answers the same question once: the same tuple object back.
    assert course.curve(constants, INNER, OUTER, 3.0 + 1e-4) is samples


def test_the_calendar_names_the_window(constants: Constants) -> None:
    """Day by day, the cheapest passage: it dips at the window and comes back
    every synodic period."""
    days = course.calendar(constants, INNER, OUTER, 0.0, 30)
    assert [d.day for d in days] == list(range(30))
    best = min(days, key=lambda d: d.dv)
    worst = max(days, key=lambda d: d.dv)
    assert worst.dv > best.dv * 1.5
    period = course.synodic_days(INNER, OUTER)
    assert period == pytest.approx(40 / 3)
    again = min((d for d in days if abs(d.day - best.day) >= period / 2), key=lambda d: d.dv)
    assert again.dv == pytest.approx(best.dv, rel=0.15), (
        "окно возвращается раз в синодический период"
    )


def test_fuel_goes_by_mass_speed_and_class(constants: Constants) -> None:
    ton = 1000.0
    one = course.fuel_for_speed(constants, ton, 10.0, efficiency=1.0)
    assert one == pytest.approx(constants[R.SHIP_FUEL_PER_TON_SPEED] * 10)
    assert course.fuel_for_speed(constants, 2 * ton, 10.0, efficiency=1.0) == pytest.approx(2 * one)
    assert course.fuel_for_speed(constants, ton, 20.0, efficiency=1.0) == pytest.approx(2 * one)
    assert course.fuel_for_speed(constants, ton, 10.0, efficiency=0.5) == pytest.approx(one / 2)
