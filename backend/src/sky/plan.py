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
from src.sky._base import (
    Body,
    Drifter,
    Rows,
    System,
    Target,
    circle_speed,
    norms,
    park_of,
    place,
    place_any,
    star_circle,
)
from src.sky.flyby import search
from src.sky.guide import BRAKE_SHARE, eject_wait
from src.sky.shoot import Shot, refine
from src.units import HOURS_PER_DAY, MINUTES_PER_HOUR, TRACE_POINTS


@dataclass(frozen=True, slots=True)
class Pass:
    """The flyby a point of the slider is flown through (D-341): what the
    order carries so the helm flies the pass that was priced."""

    #: The world lent the pull.
    via: str
    #: The periapsis, days after the departure the plan was laid from, and
    #: its radius, signed with the sense of the pass.
    at: float
    rp: float
    #: The departure's aim, relative to the world at `at` (`shoot.Shot.aim`).
    aim: tuple[float, float]
    #: The planned change of speed at the periapsis, signed, and what it costs.
    burn: float
    cost: float


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
    #: The velocity the arc leaves with, heliocentric: what the ejection
    #: window is measured against (D-316). Nought where there is no arc --
    #: a hull met on its coast.
    v1: tuple[float, float] = (0.0, 0.0)
    #: The wait for that window before the arc begins, hours. Counted here so
    #: the console shows the whole time before the button is pressed, and kept
    #: apart from `hours` because the engines' reach over the arc is the arc's
    #: own hours -- a wait lengthens the passage, not the burn.
    wait: float = 0.0
    #: The pass, for a passage bent round a third world (D-341); nothing for
    #: a direct arc. `dv` then counts the burn at the periapsis as well.
    via: Pass | None = None


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


def circle_quote(
    system: System, r0: tuple[float, float], v0: tuple[float, float], a_max: float
) -> Sample:
    """The price of the astrocentric orbit (D-289, 2026-09-04): the speed the
    hull differs by from the circle round the star through its own place,
    and the hours to shed it at full thrust. No arc: the hull stays where it
    is and changes only how it moves."""
    dv = float(np.hypot(*(star_circle(system, r0) - np.array(v0, dtype=float))))
    hours = max(dv / a_max * HOURS_PER_DAY if a_max > 0 else 0.0, LEAST_HOURS)
    here = (float(r0[0]), float(r0[1]))
    return Sample(hours=hours, dv_out=dv, dv_in=0.0, dv=dv, trace=(here, here), revs=0)


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
                escape_dv(leaving, park_of(system, leaving), excess_out)
                if leaving is not None
                else leg.dv_out
            )
            dv_in = (
                escape_dv(target, park_of(system, target), leg.dv_in)
                if isinstance(target, Body)
                else leg.dv_in
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
                v1=(float(leg.v1[0]), float(leg.v1[1])),
                wait=eject_wait(system, target, t0, r0, v0, leg.v1) * HOURS_PER_DAY,
            )
        )
    return found


def flybys(
    system: System,
    r0: tuple[float, float],
    v0: tuple[float, float],
    t0: float,
    target: Body,
    hours: tuple[float, ...],
    *,
    leaving: Body | None,
    ceiling: float,
    floor_radii: float,
) -> list[Sample]:
    """The slider's bent points (D-341): for every hour, the cheapest flyby
    that exists under five bodies and beats the direct arc of that hour.

    Laid from the centre of the world a moored hull leaves -- where it sits on
    the circle decides only its wait, which the caller adds -- or from a
    drifting hull's own state. Hours up to `ceiling` compete with the direct
    arc; past it the direct slider ends (D-317) and a flyby is the only
    passage offered. The conics name the candidates (`flyby.search`), the
    whole sky decides which of them exist (`shoot.refine`); an hour whose
    best world does not survive the refinement is tried through the next.
    """
    start = place(leaving, t0)[0][0] if leaving is not None else np.asarray(r0, dtype=float)
    here = (float(start[0]), float(start[1]))
    base = place(leaving, t0)[1][0] if leaving is not None else np.asarray(v0, dtype=float)
    beside = (float(base[0]), float(base[1]))
    direct = {
        one.hours: one.dv
        for one in preview(
            system,
            None,
            here,
            beside,
            t0,
            target,
            tuple(one for one in hours if one <= ceiling),
            leaving=leaving,
        )
    }
    found = search(
        system,
        here,
        beside,
        t0,
        target,
        hours,
        leaving=leaving,
        floor_radii=floor_radii,
        shortest=hours[0] / HOURS_PER_DAY,
    )
    #: Each hour's candidates that could still win, cheapest first. Past the
    #: ceiling there is no direct arc of the same hour to beat, and what a
    #: point must beat instead is every shorter one: a passage both longer
    #: and dearer than one already offered is no choice (D-341).
    queue = {
        hour: [one for one in kept if one.dv < direct.get(hour, np.inf)]
        for hour, kept in found.items()
        if hour in direct
    }
    cheapest = min(direct.values(), default=np.inf)
    for hour in sorted(found):
        if hour <= ceiling:
            continue
        queue[hour] = [one for one in found[hour] if one.dv < cheapest]
        cheapest = min([cheapest, *(one.dv for one in queue[hour][:1])])
    shots: dict[float, Shot] = {}
    while True:
        batch = [kept.pop(0) for hour, kept in sorted(queue.items()) if kept and hour not in shots]
        if not batch:
            break
        for one, shot in zip(
            batch,
            refine(
                system,
                here,
                beside,
                t0,
                target,
                batch,
                leaving=leaving,
                floor_radii=floor_radii,
            ),
            strict=True,
        ):
            if shot is not None and shot.dv < direct.get(one.hours, np.inf):
                shots[one.hours] = shot
    #: The same rule on the refined prices: the conics named the candidates,
    #: the whole sky priced them.
    cheapest = min(
        [*direct.values(), *(shot.dv for hour, shot in shots.items() if hour <= ceiling)],
        default=np.inf,
    )
    offered: list[Sample] = []
    for hour, shot in sorted(shots.items()):
        if hour > ceiling:
            if shot.dv >= cheapest:
                continue
            cheapest = shot.dv
        offered.append(_bent(system, here, t0, target, shot))
    return offered


def _bent(
    system: System, start: tuple[float, float], t0: float, target: Body, shot: Shot
) -> Sample:
    """A refined flyby as a point of the slider, with the line the chart draws:
    two arcs round the star meeting at the world, the kink where the pass is."""
    one = shot.candidate
    tof = one.hours / HOURS_PER_DAY
    via = system.body(one.via)
    corner, corner_v = place(via, t0 + shot.at)
    corner_r = (float(corner[0, 0]), float(corner[0, 1]))
    goal = place(target, t0 + tof)[0][0]
    first = _arc(system.mu, start, corner_r, shot.at, shot.v1)
    #: The second arc leaves the world with the excess the conics named, turned
    #: back into the star's frame: it picks the way round the refinement flew.
    out = (corner_v[0, 0] + one.v_out[0], corner_v[0, 1] + one.v_out[1])
    second = _arc(system.mu, corner_r, (float(goal[0]), float(goal[1])), tof - shot.at, out)
    head = max(2, min(TRACE_POINTS - 1, round(TRACE_POINTS * shot.at / tof)))
    trace = (
        astro.trace(system.mu, start, first, shot.at, head)
        + astro.trace(system.mu, corner_r, second, tof - shot.at, TRACE_POINTS - head + 1)[1:]
    )
    return Sample(
        hours=one.hours,
        dv_out=shot.dv_out,
        dv_in=shot.dv_in,
        dv=shot.dv,
        trace=trace,
        revs=0,
        v1=shot.v1,
        via=Pass(
            via=one.via,
            at=shot.at,
            rp=shot.rp,
            aim=shot.aim,
            burn=shot.speed_out - shot.speed_in,
            cost=shot.dv_pass,
        ),
    )


def _arc(
    mu: float,
    here: tuple[float, float],
    there: tuple[float, float],
    tof: float,
    near: tuple[float, float],
) -> tuple[float, float]:
    """The zero-turn arc between two points whose departure velocity is
    nearest `near` -- either way round; `near` itself where none exists."""
    best, gap = near, np.inf
    for retrograde in (False, True):
        for v1, _ in astro.lambert(mu, here, there, tof, 0, retrograde=retrograde):
            miss = float(np.hypot(v1[0] - near[0], v1[1] - near[1]))
            if miss < gap:
                best, gap = v1, miss
    return best


def _pair(placed: tuple[Rows, Rows]) -> tuple[tuple[float, float], tuple[float, float]]:
    r, v = placed
    return (float(r[0, 0]), float(r[0, 1])), (float(v[0, 0]), float(v[0, 1]))


def _circle(r0: tuple[float, float]) -> astro.Orbit:
    """A circular orbit through `r0`, for the turn count bound: `_max_revs`
    wants an orbit and reads only its radius."""
    return (float(np.hypot(*r0)), 1.0, 0.0)
