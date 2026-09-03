# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The plan of a passage (D-289): what the slider offers, and the one
trajectory the order is flown by.

Two readings, deliberately unequal in cost:

* **the preview** -- every point of the slider priced as D-271 prices it: a
  Lambert arc round the star from where the hull is to where the planet
  will be, plus what leaving the parking circle and settling onto the far
  one cost by patched conics. Cheap, and drawn as the two-body arc; the
  chart redraws it as the slider moves;
* **the refinement** -- the chosen point flown under the full pull of all
  five bodies, the departure velocity corrected by shooting until the hull
  arrives where the planet will be. This is the line the order carries and
  the map draws the hull along; the autopilot re-plans from it every tick.

The plan is an approximation and the simulation is the truth (D-289): a
plan is what one pays for, the tick is what one gets.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src import astro
from src.constants import Constants
from src.sky._base import Body, Rows, System, circle_speed, norms, place
from src.units import HOURS_PER_DAY, TRACE_POINTS


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
    target: Body,
    hours: tuple[float, ...],
    *,
    leaving: Body | None,
) -> list[Sample]:
    """The slider: the cheapest arc for each flight time, priced at both ends.

    `leaving` is the planet whose parking circle the hull sits on, or nothing
    for a hull adrift: only a parked hull pays to escape, and it pays by the
    excess over its planet's speed rather than over its own.
    """
    corona = system.corona
    found: list[Sample] = []
    here = (r0, v0)
    for span in hours:
        tof = span / HOURS_PER_DAY
        there = _pair(place(target, t0 + tof))
        best = None
        for leg in astro.legs(
            system.mu,
            here,
            there,
            tof,
            max_revs=astro.max_revs(system.mu, _circle(r0), target.orbit, tof),
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
            dv_in = escape_dv(target, system.park, leg.dv_in)
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
