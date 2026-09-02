# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The way between worlds, as the sky prices it (D-271).

The rules of a passage on top of `astro`'s arithmetic: nothing here reads a
row. Planets go round the star on circular Keplerian orbits, and a passage
between two of them is a **Lambert arc** -- the conic that leaves the
departure planet's place at the moment of casting off and reaches the target
planet's place at the moment of arrival, in exactly the flight time chosen.
Two burns close it: one to leave the departure planet's orbital speed, one to
match the target's. Their sum is the delta-v the tanks pay for.

Units are the map's: a length is the vault's orbit radius unit, a time is a
real day, a speed is units per day. The star's gravitational parameter is not
a constant of the vault but a property of the orbits themselves (Kepler's
third law), so that the two can never disagree.

What lives here:

- `arc`        -- the cheapest arc for a flight time, with its trace;
- `flyby`      -- the same passage bent round a third planet (patched conics);
- `curve`      -- delta-v against flight time, the slider the console shows;
- `calendar`   -- the cheapest passage for each of the coming days, the map's
                  window forecast;
- `deliverable`, `fuel_for_speed` -- what the hull's thrust and class make of
  a delta-v.
"""

from __future__ import annotations

from functools import lru_cache
from typing import NamedTuple

from src import astro
from src.astro import Orbit, Vec, mu_of, place
from src.constants import Constants
from src.constants import registry as R
from src.units import (
    HOURS_PER_DAY,
    KG_PER_TON,
    PERCENT,
    ROUND_DV,
    ROUND_HOURS,
    SKY_CALENDAR_MEMO,
    SKY_CURVE_MEMO,
    SKY_MEMO_PER_DAY,
    TRACE_POINTS,
)

__all__ = [
    "Arc",
    "Day",
    "Orbit",
    "Sample",
    "arc",
    "calendar",
    "cheapest",
    "curve",
    "deliverable",
    "fastest",
    "flyby",
    "fuel_for_speed",
    "grid",
    "mu_of",
    "place",
    "synodic_days",
]


class Sample(NamedTuple):
    """One point of the slider: a flight time and what the sky asks for it.

    `via` names the planet the arc bends round, or nothing for a direct arc;
    `revs` is how many full turns round the star the arc makes before it
    arrives -- the slow way at a bad geometry loiters on its own orbit first.
    """

    hours: float
    dv: float
    via: str | None = None
    revs: int = 0


class Arc(NamedTuple):
    """The chosen arc in full: the burns, the geometry, and the line to draw."""

    hours: float
    dv: float
    #: Speed to shed at each end: to leave, and to match on arrival.
    dv_out: float
    dv_in: float
    perihelion: float
    revs: int
    via: str | None
    #: Points along the way at equal time steps, map units: the map draws the
    #: hull on the arc and finds its place by the share of the time gone.
    trace: tuple[Vec, ...]


def synodic_days(one: Orbit, other: Orbit) -> float:
    """How often the two planets meet: `Ta Tb / |Ta - Tb|` days."""
    return astro.synodic(one, other)


# --- one passage ------------------------------------------------------------


class _Leg(NamedTuple):
    v1: Vec
    v2: Vec
    dv_out: float
    dv_in: float
    perihelion: float
    revs: int


def _legs(
    mu: float, here: tuple[Vec, Vec], there: tuple[Vec, Vec], tof: float, *, max_revs: int
) -> list[_Leg]:
    """Every arc between two moving places, priced at both ends."""
    (r1, vp1), (r2, vp2) = here, there
    legs: list[_Leg] = []
    #: Both ways round for a direct arc; against the planets only a fast hull
    #: gains, and a fast hull does not make full turns.
    ways = [(revs, False) for revs in range(max_revs + 1)] + [(0, True)]
    for revs, retrograde in ways:
        for v1, v2 in astro.lambert(mu, r1, r2, tof, revs, retrograde=retrograde):
            legs.append(
                _Leg(
                    v1,
                    v2,
                    astro.norm(astro.sub(v1, vp1)),
                    astro.norm(astro.sub(v2, vp2)),
                    astro.closest(mu, r1, v1, r2, v2, revs),
                    revs,
                )
            )
    return legs


def _max_revs(mu: float, here: Orbit, there: Orbit, tof: float) -> int:
    """How many full turns a flight this long could possibly make.

    A bound, not an answer: an arc cannot turn faster than a circle at the
    inner of the two radii, so more turns than that fit in `tof` is not worth
    asking the solver about.
    """
    inner = min(here[0], there[0])
    return min(astro.MAX_REVS, int(tof / astro.lap(mu, inner)))


def arc(
    constants: Constants,
    here: Orbit,
    there: Orbit,
    days: float,
    hours: float,
) -> Arc | None:
    """The cheapest direct arc from `here` to `there` leaving at `days`, flying
    `hours`. None if no arc exists -- every one grazes the corona, or the
    geometry gives nothing for that time."""
    mu = mu_of(here)
    tof = hours / HOURS_PER_DAY
    corona = float(constants[R.ORBIT_CORONA_RADIUS])
    start = place(here, days)
    end = place(there, days + tof)
    best: _Leg | None = None
    for leg in _legs(mu, start, end, tof, max_revs=_max_revs(mu, here, there, tof)):
        if leg.perihelion < corona:
            continue
        if best is None or leg.dv_out + leg.dv_in < best.dv_out + best.dv_in:
            best = leg
    if best is None:
        return None
    return Arc(
        hours=hours,
        dv=best.dv_out + best.dv_in,
        dv_out=best.dv_out,
        dv_in=best.dv_in,
        perihelion=best.perihelion,
        revs=best.revs,
        via=None,
        trace=astro.trace(mu, start[0], best.v1, tof, TRACE_POINTS),
    )


# --- a flyby ----------------------------------------------------------------


def _bend(
    constants: Constants,
    here: Orbit,
    middle: Orbit,
    there: Orbit,
    days: float,
    hours: float,
    *,
    gravity: float,
) -> tuple[float, _Leg, _Leg, float] | None:
    """The cheapest split of the flight round `middle`: total delta-v, the two
    legs and the length of the first. The search `flyby` builds its arc on."""
    mu = mu_of(here)
    mu_planet = float(constants[R.ORBIT_FLYBY_MU]) * gravity
    closest_pass = float(constants[R.ORBIT_FLYBY_RADIUS])
    corona = float(constants[R.ORBIT_CORONA_RADIUS])
    tof = hours / HOURS_PER_DAY
    start = place(here, days)
    end = place(there, days + tof)
    best: tuple[float, _Leg, _Leg, float] | None = None
    for share in astro.FLYBY_SPLITS:
        first = tof * share
        second = tof - first
        mid = place(middle, days + first)
        for leg1 in _legs(mu, start, mid, first, max_revs=0):
            if leg1.perihelion < corona:
                continue
            for leg2 in _legs(mu, mid, end, second, max_revs=0):
                if leg2.perihelion < corona:
                    continue
                v_in = astro.sub(leg1.v2, mid[1])
                v_out = astro.sub(leg2.v1, mid[1])
                turn = astro.turn_cost(mu_planet, closest_pass, v_in, v_out)
                total = leg1.dv_out + turn + leg2.dv_in
                if best is None or total < best[0]:
                    best = (total, leg1, leg2, first)
    return best


def flyby(
    constants: Constants,
    here: Orbit,
    middle: Orbit,
    there: Orbit,
    days: float,
    hours: float,
    via: str,
    *,
    gravity: float = 1.0,
) -> Arc | None:
    """The passage bent round `middle`: two direct arcs and a turn between them.

    `gravity` is the flyby planet's share of Terra's (`planet.gravity`); the
    caller passes it, this module knows no planets by name.
    """
    best = _bend(constants, here, middle, there, days, hours, gravity=gravity)
    if best is None:
        return None
    total, leg1, leg2, first = best
    mu = mu_of(here)
    tof = hours / HOURS_PER_DAY
    start = place(here, days)
    mid = place(middle, days + first)
    return Arc(
        hours=hours,
        dv=total,
        dv_out=leg1.dv_out,
        dv_in=leg2.dv_in,
        perihelion=min(leg1.perihelion, leg2.perihelion),
        revs=0,
        via=via,
        trace=astro.trace(mu, start[0], leg1.v1, first, TRACE_POINTS)
        + astro.trace(mu, mid[0], leg2.v1, tof - first, TRACE_POINTS)[1:],
    )


# --- the slider -------------------------------------------------------------


def grid(constants: Constants) -> tuple[float, ...]:
    """Flight times the curve is sampled at, hours: geometric, a vault's share
    a step, from the vault's shortest arc out to its horizon."""
    return _grid(
        float(constants[R.ORBIT_SLIDER_FROM_HOURS]),
        1 + float(constants[R.ORBIT_SLIDER_STEP]) / PERCENT,
        float(constants[R.ORBIT_LONGEST_DAYS]) * HOURS_PER_DAY,
    )


def _grid(start: float, ratio: float, top: float) -> tuple[float, ...]:
    hours: list[float] = []
    h = start
    while h < top:
        hours.append(round(h, ROUND_HOURS))
        h *= ratio
    hours.append(top)
    return tuple(hours)


def curve(
    constants: Constants,
    here: Orbit,
    there: Orbit,
    days: float,
    *,
    others: dict[str, tuple[Orbit, float]] | None = None,
) -> tuple[Sample, ...]:
    """Delta-v against flight time, from the departure moment `days`.

    One sample per point of the grid: the cheapest arc at that time, direct or
    bent round one of `others` (orbit and gravity by planet key) when that
    comes out cheaper. Missing samples are times no arc serves -- everything
    grazes the corona, or the geometry gives nothing.

    Planetary and nothing else: what the hull can do with it is `deliverable`
    and the tanks. Memoised, because every hull over a planet asks the same
    question of the same sky, and the sky moves little in ten minutes.
    """
    key_others = () if not others else tuple(sorted((k, o, g) for k, (o, g) in others.items()))
    return _curve(
        _fingerprint(constants),
        here,
        there,
        round(days * SKY_MEMO_PER_DAY) / SKY_MEMO_PER_DAY,
        key_others,
    )


def _fingerprint(constants: Constants) -> tuple[float, ...]:
    """The constants the curve depends on, as a hashable key: an edit in the
    admin panel must not serve yesterday's curve."""
    return (
        float(constants[R.ORBIT_CORONA_RADIUS]),
        float(constants[R.ORBIT_LONGEST_DAYS]),
        float(constants[R.ORBIT_FLYBY_MU]),
        float(constants[R.ORBIT_FLYBY_RADIUS]),
        float(constants[R.ORBIT_SLIDER_FROM_HOURS]),
        float(constants[R.ORBIT_SLIDER_STEP]),
        float(constants[R.ORBIT_CALENDAR_STEP]),
    )


class _Bare(dict):
    """The few constants the memoised curve needs, in the shape `constants[R.X]`
    takes -- so the same `arc`/`_bend` serve both the live call and the memo."""


def _thaw(fingerprint: tuple[float, ...]) -> _Bare:
    corona, longest, flyby_mu, flyby_radius, slider_from, slider_step, calendar_step = fingerprint
    return _Bare(
        {
            R.ORBIT_CORONA_RADIUS: corona,
            R.ORBIT_LONGEST_DAYS: longest,
            R.ORBIT_FLYBY_MU: flyby_mu,
            R.ORBIT_FLYBY_RADIUS: flyby_radius,
            R.ORBIT_SLIDER_FROM_HOURS: slider_from,
            R.ORBIT_SLIDER_STEP: slider_step,
            R.ORBIT_CALENDAR_STEP: calendar_step,
        }
    )


@lru_cache(maxsize=SKY_CURVE_MEMO)
def _curve(
    fingerprint: tuple[float, ...],
    here: Orbit,
    there: Orbit,
    days: float,
    others: tuple[tuple[str, Orbit, float], ...],
) -> tuple[Sample, ...]:
    constants = _thaw(fingerprint)
    corona = float(constants[R.ORBIT_CORONA_RADIUS])
    mu = mu_of(here)
    samples: list[Sample] = []
    start = place(here, days)
    for hours in grid(constants):
        tof = hours / HOURS_PER_DAY
        end = place(there, days + tof)
        best: Sample | None = None
        for leg in _legs(mu, start, end, tof, max_revs=_max_revs(mu, here, there, tof)):
            if leg.perihelion < corona:
                continue
            total = leg.dv_out + leg.dv_in
            if best is None or total < best.dv:
                best = Sample(hours, total, None, leg.revs)
        for name, orbit, gravity in others:
            bent = _bend(constants, here, orbit, there, days, hours, gravity=gravity)
            if bent is not None and (best is None or bent[0] < best.dv):
                best = Sample(hours, bent[0], name, 0)
        if best is not None:
            samples.append(best)
    return tuple(samples)


def flown(points: list[list[float]], share: float) -> list[list[float]]:
    """The part of a traced arc covered by `share` of its time, the last point
    interpolated -- what a turn-back reverses (D-242, D-271).

    The points are at equal time steps, so the share picks the segment and the
    remainder interpolates inside it: the same rule the map draws the hull by.
    """
    last = len(points) - 1
    if last < 1:
        return list(points)
    held = min(1.0, max(0.0, share))
    at = held * last
    i = min(last - 1, int(at // 1))
    rest = at - i
    head = [list(p) for p in points[: i + 1]]
    tip = [
        points[i][0] + (points[i + 1][0] - points[i][0]) * rest,
        points[i][1] + (points[i + 1][1] - points[i][1]) * rest,
    ]
    if rest > 0:
        head.append(tip)
    return head


def cheapest(samples: tuple[Sample, ...]) -> Sample | None:
    """The slow end of the slider: the least delta-v the horizon offers."""
    return min(samples, key=lambda s: s.dv, default=None)


# --- what the hull makes of it ------------------------------------------------


def deliverable(constants: Constants, thrust_ratio: float, hours: float) -> float:
    """The most delta-v the engines can give in a flight this long.

    Thrust against mass times the vault's scale is the hull's acceleration;
    the engines may burn for `orbit.burn_share` of the flight. A heavy hull
    cannot fly fast -- which is what the stretched table time used to say
    (D-202), and now says in the sky's own units.
    """
    scale = float(constants[R.ORBIT_THRUST_SCALE])
    share = float(constants[R.ORBIT_BURN_SHARE])
    return thrust_ratio * scale * hours / HOURS_PER_DAY * share


def fuel_for_speed(constants: Constants, weight: float, dv: float, *, efficiency: float) -> float:
    """Fuel for a delta-v: by mass, by speed, and by the class of what pushes.

    Linear in both -- the impulse model, not the rocket equation: readable at
    the console and the same shape as the legs' `fuel_for`. `efficiency` is
    the class's share of the baseline burn (`physics.efficiency`).
    """
    return float(constants[R.SHIP_FUEL_PER_TON_SPEED]) * weight / KG_PER_TON * dv * efficiency


def fastest(
    constants: Constants, samples: tuple[Sample, ...], thrust_ratio: float
) -> Sample | None:
    """The fast end of the slider: the shortest flight the engines can deliver."""
    for sample in samples:
        if sample.dv <= deliverable(constants, thrust_ratio, sample.hours):
            return sample
    return None


# --- the map's forecast ---------------------------------------------------------


class Day(NamedTuple):
    """One day of the corridor's calendar: the cheapest passage leaving then."""

    day: int
    dv: float
    hours: float


def calendar(
    constants: Constants, here: Orbit, there: Orbit, day0: float, days: int
) -> tuple[Day, ...]:
    """The cheapest direct passage for each of the coming `days`.

    Coarser than the slider on purpose (`orbit.calendar_step`): it is a
    picture of when the window opens, not a quote, and it is drawn for every
    pair on every map read.
    """
    return _calendar(_fingerprint(constants), here, there, int(day0 // 1), days)


@lru_cache(maxsize=SKY_CALENDAR_MEMO)
def _calendar(
    fingerprint: tuple[float, ...], here: Orbit, there: Orbit, day0: int, days: int
) -> tuple[Day, ...]:
    constants = _thaw(fingerprint)
    corona = float(constants[R.ORBIT_CORONA_RADIUS])
    mu = mu_of(here)
    hours_grid = _grid(
        float(constants[R.ORBIT_SLIDER_FROM_HOURS]),
        1 + float(constants[R.ORBIT_CALENDAR_STEP]) / PERCENT,
        float(constants[R.ORBIT_LONGEST_DAYS]) * HOURS_PER_DAY,
    )
    out: list[Day] = []
    for offset in range(days):
        at = day0 + offset
        start = place(here, at)
        best: tuple[float, float] | None = None
        for hours in hours_grid:
            tof = hours / HOURS_PER_DAY
            end = place(there, at + tof)
            for leg in _legs(mu, start, end, tof, max_revs=_max_revs(mu, here, there, tof)):
                if leg.perihelion < corona:
                    continue
                total = leg.dv_out + leg.dv_in
                if best is None or total < best[0]:
                    best = (total, hours)
        if best is not None:
            out.append(Day(at, round(best[0], ROUND_DV), round(best[1], ROUND_HOURS)))
    return tuple(out)
