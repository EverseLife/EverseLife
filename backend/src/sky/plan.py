# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The plan of a passage (D-289): what the slider offers, and the line the
order carries.

Two things, deliberately unequal in cost:

* **the preview** -- every point of the slider priced as D-271 prices it: a
  Lambert arc round the star from where the hull is to where the planet
  will be, plus what leaving the parking circle and settling onto the far
  one cost by patched conics. Cheap, and drawn as the two-body arc; the
  chart redraws it as the slider moves;
* **the order's line** -- the preview's own two-body arc, no more. A
  refinement by shooting under all five bodies was built and dropped in the
  same wave: it diverged on the cheap end of the slider and bought only a
  picture, since the helm re-solves the passage from where the hull is every
  tick whatever line was drawn at the order.

The plan is an approximation and the simulation is the truth (D-289): a
plan is what one pays for, the tick is what one gets.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src import astro
from src.constants import Constants
from src.sky._base import Body, Drifter, Rows, System, Target, circle_speed, norms, place, place_any
from src.sky.guide import BRAKE_SHARE
from src.units import HOURS_PER_DAY, MINUTES_PER_HOUR, TRACE_POINTS


@dataclass(frozen=True, slots=True)
class Sample:
    """One point of the slider, priced."""

    hours: float
    #: What leaving costs, what arriving costs, and their sum -- units a day.
    dv_out: float
    dv_in: float
    dv: float
    #: The two-body arc for the chart, map units at equal time steps.
    trace: tuple[tuple[float, float], ...]
    #: Full turns round the star before arrival (D-271).
    revs: int


#: The least an order may promise: a minute. A hull already alongside is
#: captured on its first step, and an order needs an hour above zero to be
#: an order at all.
LEAST_HOURS = 1.0 / MINUTES_PER_HOUR


def approach_quote(
    r0: tuple[float, float],
    v0: tuple[float, float],
    t0: float,
    target: Drifter,
    a_max: float,
) -> Sample:
    """The one price of going to a hull (D-289, wave 3): what the approach
    profile the helm flies (`guide._meet`) will take and burn.

    No slider: the helm toward a hull does not chase an arc to a planned
    hour, it closes the gap along a profile bounded by the thrust -- so the
    honest quote is the profile's own. Accelerate toward the hull at full
    thrust, brake to rest beside it with the profile's share of it, plus
    what shedding the speed the two differ by costs: twice the peak speed
    the gap allows, and the hours to reach and shed it.
    """
    p, vp = place_any(target, t0)
    rel = np.array(r0) - p[0]
    v_rel = np.array(v0) - vp[0]
    gap = float(np.hypot(*rel))
    speed = float(np.hypot(*v_rel))
    push = BRAKE_SHARE * a_max
    if a_max <= 0:
        peak, days = 0.0, 0.0
    else:
        #: Where the run-up at `a_max` meets the run-down at `push`.
        peak = float(np.sqrt(2.0 * gap / (1.0 / a_max + 1.0 / push)))
        days = peak / a_max + peak / push + speed / a_max
    dv = 2 * peak + speed
    hours = max(days * HOURS_PER_DAY, LEAST_HOURS)
    there = place_any(target, t0 + hours / HOURS_PER_DAY)[0][0]
    return Sample(
        hours=hours,
        dv_out=dv,
        dv_in=0.0,
        dv=dv,
        trace=(tuple(float(x) for x in r0), (float(there[0]), float(there[1]))),
        revs=0,
    )


def escape_dv(body: Body, park: float, v_inf: float) -> float:
    """What it costs to leave the parking circle with this excess (patched
    conics): the speed at periapsis of the hyperbola less the circle's."""
    return float(np.sqrt(v_inf * v_inf + 2 * body.mu / park) - circle_speed(body, park))


def preview(
    system: System,
    constants: Constants,
    r0: tuple[float, float],
    v0: tuple[float, float],
    t0: float,
    target: Target,
    hours: tuple[float, ...],
    *,
    leaving: Body | None,
) -> list[Sample]:
    """The slider: the cheapest arc for each flight time, priced at both ends.

    `leaving` is the planet whose parking circle the hull sits on, or nothing
    for a hull adrift: only a parked hull pays to escape, and it pays by the
    excess over its planet's speed rather than over its own. A drifter as the
    target (D-289, wave 3) is met on its forecast: the arrival pays the whole
    difference of speed, there being no circle to settle onto.
    """
    corona = system.corona
    found: list[Sample] = []
    here = (r0, v0)
    for span in hours:
        tof = span / HOURS_PER_DAY
        there = _pair(place_any(target, t0 + tof))
        best = None
        far = target.orbit if isinstance(target, Body) else _circle(there[0])
        for leg in astro.legs(
            system.mu,
            here,
            there,
            tof,
            max_revs=astro.max_revs(system.mu, _circle(r0), far, tof),
        ):
            if leg.perihelion < corona:
                continue
            excess_out = (
                float(norms(np.array([leg.v1]) - place(leaving, t0)[1])[0])
                if leaving is not None
                else leg.dv_out
            )
            dv_out = (
                escape_dv(leaving, system.park, excess_out) if leaving is not None else leg.dv_out
            )
            dv_in = (
                escape_dv(target, system.park, leg.dv_in) if isinstance(target, Body) else leg.dv_in
            )
            if best is None or dv_out + dv_in < best[0]:
                best = (dv_out + dv_in, dv_out, dv_in, leg)
        if best is None:
            continue
        total, dv_out, dv_in, leg = best
        found.append(
            Sample(
                hours=span,
                dv_out=dv_out,
                dv_in=dv_in,
                dv=total,
                trace=astro.trace(system.mu, r0, leg.v1, tof, TRACE_POINTS),
                revs=leg.revs,
            )
        )
    return found


def _pair(placed: tuple[Rows, Rows]) -> tuple[tuple[float, float], tuple[float, float]]:
    r, v = placed
    return (float(r[0, 0]), float(r[0, 1])), (float(v[0, 0]), float(v[0, 1]))


def _circle(r0: tuple[float, float]) -> astro.Orbit:
    """A circular orbit through `r0`, for the turn count bound: `_max_revs`
    wants an orbit and reads only its radius."""
    return (float(np.hypot(*r0)), 1.0, 0.0)
