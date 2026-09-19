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
  refinement by shooting under all five bodies was built and dropped for the
  direct arc in the same wave: it diverged on the cheap end of the slider and
  bought only a picture, since the helm re-solves the passage from where the
  hull is every tick whatever line was drawn at the order.

A flyby is the exception, and a deliberate one (D-341): near Pyroxis the
conics name a pass that is not there, so the slider's bent points are refined
in the whole sky before they are offered (`routes`, `sky.shoot`) -- flown out
of the periapsis both ways, which converges where a shot aimed at the world
did not -- and the order carries the pass the refinement found. What of all
that one hull is offered is `sky.choice`'s to say.

The plan is an approximation and the simulation is the truth (D-289): a
plan is what one pays for, the tick is what one gets.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
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
from src.sky.choice import choices, deliverable, front
from src.sky.flyby import Candidate, search
from src.sky.guide import BRAKE_SHARE, eject_wait
from src.sky.rendezvous import arc_to, flown_as_priced
from src.sky.shoot import Shot, refine
from src.units import HOURS_PER_DAY, MINUTES_PER_HOUR, TRACE_POINTS

#: The numerics' guard on the outward search for flybys (D-341), in years of
#: the slowest world: no window reaches past two of them. A limit of the
#: computation, and one that does bound what is offered: a chain of choices
#: that ran past a year of the slowest world would lose its windows beyond
#: the guard. On the sky of 2026-09-13 the chain ended by 106 days at most and
#: the search by 207, against Aurora's 260; a window the guard cuts is logged.
_SEARCH_YEARS = 2.0

#: How far under its conic price a refined flyby can come out: over 324
#: refinements from 96 departures measured 2026-09-13 the sky's price was the
#: conic's own in the median, under 0.9 of it in one in twenty, and 0.68 of it
#: at the least. A candidate is refined while its conic price times this still
#: beats what a choice must beat. Against refining by the bare conic price it
#: changed 20 sliders of 144 in the re-measure of D-341 -- 9 cheap ends came
#: out cheaper, 9 reached further. The ratio follows the vault's worlds
#: (`planet.mass`, `orbit.planet_mu`, `orbit.flyby_floor_radii`): a pass the
#: sky confirms further under its conic than this is logged.
_CONIC_SPREAD = 0.68

_LOG = logging.getLogger(__name__)


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
    #: The planet an arc goes round, for a meeting in orbit (D-354, wave 3):
    #: the trace is then relative to that planet's centre -- one lap of the
    #: arc's orbit -- and the chart puts it where the planet is. Nothing for
    #: an arc round the star and for the straight approach to a hull, whose
    #: one price is its profile's (`approach_quote`).
    around: str | None = None


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


def meet_quotes(
    system: System,
    body: Body,
    t0: float,
    r0: tuple[float, float],
    v0: tuple[float, float],
    target: Drifter,
    hours: Sequence[float],
    a_max: float,
) -> list[Sample]:
    """The slider to a hull in orbit round the same planet (D-354, wave 3):
    for every flight time of the grid, the cheapest arc round the planet to
    where the other hull will be then (`rendezvous.arc_to`), priced at both
    ends. Unlike the straight approach there is a real choice here -- the
    more laps the hours hold, the less speed is changed -- so it is a slider
    like a planet's, and what of it one hull is offered is `choice`'s. An arc
    whose burns engines of `a_max` cannot fly as instants is left out
    (`rendezvous.flown_as_priced`): round a planet a long burn falls off it.

    The trace is one lap of the arc's orbit at most, round the planet's
    centre: the hull's own place is the sky's to draw (`around`)."""
    samples: list[Sample] = []
    for one in hours:
        tof = one / HOURS_PER_DAY
        found = arc_to(system, body, t0, r0, v0, target, tof)
        if found is None or not flown_as_priced(system, body, found, tof, a_max):
            continue
        samples.append(
            Sample(
                hours=one,
                dv_out=found.dv_out,
                dv_in=found.dv_in,
                dv=found.dv_out + found.dv_in,
                trace=astro.trace(
                    body.mu, found.rel, found.v_rel, min(tof, found.period), TRACE_POINTS
                ),
                revs=found.revs,
                v1=found.v1,
                around=body.key,
            )
        )
    return samples


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
    constants: Constants | None,
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


def search_days(system: System) -> float:
    """How far out the flyby search may ever reach, days: the numerics' guard
    (`_SEARCH_YEARS` of the slowest world) -- a limit of the computation, not
    a number of the game's balance."""
    return _SEARCH_YEARS * max((one.orbit[1] for one in system.bodies), default=0.0)


def routes(
    system: System,
    r0: tuple[float, float],
    v0: tuple[float, float],
    t0: float,
    target: Body,
    hours: tuple[float, ...],
    *,
    leaving: Body | None,
    longest: float,
    reach: float,
    gap: float,
    floor_radii: float,
) -> list[Sample]:
    """The slider one hull is offered to a planet (D-341): the direct arcs and
    the flybys that exist under five bodies, cut to the choices
    (`sky.choice`: what the engines deliver in `reach` a day, the real
    choices, the largest group parted at `gap`).

    Laid from the hull's own place and velocity at `t0` -- on the parking
    circle of `leaving`, or adrift -- exactly as the direct preview is: the
    plan, the wait for the ejection window and every price are this hull's
    own. `hours` is the slider's grid (`course.flyby_grid`): the direct arc
    is priced up to `longest` hours (D-317), a flyby on past them.

    **The search walks outward.** Up to `longest` every hour's flybys compete
    with the direct arcs. Past it the search goes a window at a time, each
    reaching `gap` times the hours of the slowest choice found so far -- the
    end of the chain -- and stops at the first window that adds no choice.
    A choice is cheaper than every faster route, so a window's choices do not
    depend on anything slower; and past an empty window nothing can join the
    chain, being more than `gap` times longer than its end. The groups the
    search never reaches are not seen, and the largest group is the largest
    of those it saw.

    **Refined only what could be a choice.** The conics name the candidates
    (`flyby.search`), the whole sky decides which exist (`shoot.refine`), and
    a candidate is refined only if its conic price, less the spread the sky's
    prices have shown against it (`_CONIC_SPREAD`), beats every route already
    certain at its hour or faster -- a direct arc of the engines' reach or a
    confirmed flyby -- and whatever is priced at its own hour. An hour whose
    best world does not survive the refinement is tried through the next, and
    so is one whose next world might still come out cheaper. The search is
    exact for the chain the refinements confirm; a pass the conics do not
    name at all, or name dearer than the spread, is not seen.
    """
    shortest = hours[0] / HOURS_PER_DAY
    guard = search_days(system) * HOURS_PER_DAY
    found: list[Sample] = preview(
        system,
        None,
        r0,
        v0,
        t0,
        target,
        tuple(one for one in hours if one <= longest),
        leaving=leaving,
    )
    top, bound = 0.0, longest
    while True:
        window = tuple(one for one in hours if top < one <= min(bound, guard))
        if not window:
            if bound > guard:
                #: The chain still grew, and only the guard stops it.
                _LOG.warning(
                    "flyby search cut by its guard: the chain reaches %.1f days, the guard %.1f",
                    bound / HOURS_PER_DAY,
                    guard / HOURS_PER_DAY,
                )
            break
        shots = _refine_all(
            system,
            r0,
            v0,
            t0,
            target,
            search(
                system,
                r0,
                v0,
                t0,
                target,
                window,
                leaving=leaving,
                floor_radii=floor_radii,
                shortest=shortest,
            ),
            found,
            reach=reach,
            leaving=leaving,
            floor_radii=floor_radii,
        )
        found.extend(_bent(system, r0, v0, t0, target, shot) for shot in shots)
        chain = front(found, reach=reach)
        if top > 0.0 and not any(top < one.hours for one in chain):
            break
        #: The end of the chain: the slowest choice, or the direct slider's
        #: own horizon while there is none.
        top = window[-1]
        bound = gap * (chain[-1].hours if chain else longest)
    return choices(found, reach=reach, gap=gap)


def _refine_all(
    system: System,
    r0: tuple[float, float],
    v0: tuple[float, float],
    t0: float,
    target: Body,
    queue: dict[float, list[Candidate]],
    certain: list[Sample],
    *,
    reach: float,
    leaving: Body | None,
    floor_radii: float,
) -> list[Shot]:
    """Refine each hour's candidates cheapest first, all hours in one batch a
    round, until no hour has a candidate left that could still be a choice.

    What a choice must beat is the `certain` routes (`routes`), and every
    confirmed flyby joins them for the rounds after -- its own hour's too, so
    another world's pass at that hour is refined only if it might come out
    cheaper still. Whether a candidate might is judged by its conic price
    with `_CONIC_SPREAD` taken off: the conic is no bound on what the sky
    confirms, and a choice pruned by it would cut the chain short."""
    known = list(certain)
    shots: dict[float, Shot] = {}
    while True:
        bar = _bars(known, reach)
        batch = []
        for hour, kept in sorted(queue.items()):
            kept[:] = [one for one in kept if one.dv * _CONIC_SPREAD < bar(hour)]
            if kept:
                batch.append(kept.pop(0))
        if not batch:
            return [shots[hour] for hour in sorted(shots)]
        found = refine(system, r0, v0, t0, target, batch, leaving=leaving, floor_radii=floor_radii)
        for one, shot in zip(batch, found, strict=True):
            if shot is not None and shot.dv < one.dv * _CONIC_SPREAD:
                #: The sky came out further under the conic than the spread
                #: allows for: passes pruned by it may have been choices.
                _LOG.warning(
                    "flyby refined under the conic spread: %.1f against %.1f through %s",
                    shot.dv,
                    one.dv,
                    one.via,
                )
            if shot is not None and shot.dv < bar(one.hours):
                shots[one.hours] = shot
                known.append(_priced(one.hours, shot.dv))


def _bars(known: list[Sample], reach: float) -> Callable[[float], float]:
    """What a route of each hour must cost less than to be a choice: every
    certain route the engines deliver at that hour or faster, and whatever
    else is priced at the hour itself."""
    delivered = sorted((one.hours, one.dv) for one in known if deliverable(one, reach))
    own: dict[float, float] = {}
    for one in known:
        own[one.hours] = min(own.get(one.hours, np.inf), one.dv)

    def bar(hour: float) -> float:
        faster = (dv for hours, dv in delivered if hours <= hour)
        return min(own.get(hour, np.inf), *faster, np.inf)

    return bar


def _priced(hours: float, dv: float) -> Sample:
    """A price alone, as `_bars` reads a confirmed flyby before its line is drawn."""
    return Sample(hours=hours, dv_out=0.0, dv_in=0.0, dv=dv, trace=(), revs=0)


def _bent(
    system: System,
    r0: tuple[float, float],
    v0: tuple[float, float],
    t0: float,
    target: Body,
    shot: Shot,
) -> Sample:
    """A refined flyby as a point of the slider, with the line the chart draws
    -- two arcs round the star meeting at the world, the kink where the pass
    is -- and the hull's own wait for the window it leaves by (D-316)."""
    one = shot.candidate
    tof = one.hours / HOURS_PER_DAY
    via = system.body(one.via)
    corner, corner_v = place(via, t0 + shot.at)
    corner_r = (float(corner[0, 0]), float(corner[0, 1]))
    goal = place(target, t0 + tof)[0][0]
    first = _arc(system.mu, r0, corner_r, shot.at, shot.v1)
    #: The second arc leaves the world with the excess the conics named, turned
    #: back into the star's frame: it picks the way round the refinement flew.
    out = (corner_v[0, 0] + one.v_out[0], corner_v[0, 1] + one.v_out[1])
    second = _arc(system.mu, corner_r, (float(goal[0]), float(goal[1])), tof - shot.at, out)
    head = max(2, min(TRACE_POINTS - 1, round(TRACE_POINTS * shot.at / tof)))
    trace = (
        astro.trace(system.mu, r0, first, shot.at, head)
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
        wait=eject_wait(system, target, t0, r0, v0, shot.v1) * HOURS_PER_DAY,
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
