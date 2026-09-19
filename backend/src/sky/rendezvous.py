# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A meeting in orbit (D-354, wave 3): one hull going to another round the
same planet, on an arc round that planet.

Near a planet the straight approach (`guide._meet`) is the wrong flight. Two
hulls on Terra's parking circle half a lap apart are two parking radii apart
with the planet between them, and a profile aimed along the line between
them drove through it; the distance two hulls meet at (`orbit.dock_radius`)
is most of that circle's radius, so nothing about the approach is "in the
deep" there. What is flown instead is what a pilot flies: an arc round the
planet -- Lambert's problem round the planet alone, since inside its inner
sphere the other worlds are only a tide (D-354) -- from where the hull is to
where the other will be at the hour, with as many full laps as the hours
hold. More laps, less speed changed: half a lap behind on Terra's circle is
2.4 units a day in five hours and 0.6 in twenty.

Every arc is itself an orbit that keeps (`bound.closed_orbit`): clear of the
ground by the margin a bound orbit keeps, and within the share of the Hill
radius where Kepler round the planet is the sky. The arithmetic here and the
sky the tick flies agree only there, and nowhere else is a meeting in orbit.
An arc is offered only if it is flown as priced (`flown_as_priced`): burns
that are instants for the engines, and a flight the tide does not make a
fiction of.

The helm (`guide._meet_round`) does not re-solve the arc every minute. It
coasts while the coast ends near enough the other (`coast_end`), and
otherwise flies the cheapest way to finish: the coast as it is, matched at
the hour; the arc it is on, corrected (`corrected`); or a new arc
(`arc_to`) -- the departure's.

Nothing here reads a row: states in, an arc out.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src import astro
from src.sky._base import INNER_SHARE, Body, Drifter, System, hill_of, place, place_any
from src.sky.bound import Orbiter, closed_orbit, tide_slip

#: Laps either side of the count the hull's own lap puts in the hours. Two
#: and three either side found no cheaper arc in the measure of 2026-09-19
#: (Terra, Pyroxis and Aquatica, half a lap and three tenths of a radian
#: apart, the slider's whole grid from two hours to twelve days), and each
#: lap more is four more solutions for every hull that asks.
LAP_WINDOW = 1

#: Within this of a whole number of turns, radians, the hull and the point it
#: is aimed at lie on one ray from the planet: the arc's plane is undefined
#: there, and Lambert's velocity is its radial part plus a sliver that the
#: rounding of the angle decides. Measured 2026-09-19 on a hull flying a
#: twenty-lap meeting round Terra: at a thousandth of a radian it was sent
#: three units a day off its own arc for a minute, at three hundredths it
#: read its arc to a thousandth. A hull flying an arc passes this once a
#: lap, and the helm coasts that minute -- the arc goes on under it.
SAME_RAY = 0.01

#: How far coasting on may miss the other hull, as a share of the distance
#: two hulls meet at (`orbit.dock_radius`), before the helm so much as asks
#: how else to finish. Re-solving every minute was a helm hopping between the
#: arcs of neighbouring lap counts -- Lambert's roots of many laps are
#: delicate, and a lost one for a minute sent the hull onto an arc of another
#: count, paid in full -- and it burnt ten times the price on the slider's
#: middle. The miss is measured by the two-body coast round the planet, as
#: the arc was laid; what the tide adds shows up as a miss.
AIM_SHARE = 0.05

#: The longest a burn may take, as a share of a lap of the arc it starts or
#: ends: what the arc's price assumes is an instant. Measured 2026-09-19 over
#: the offered points of every world at two thrusts, under the whole sky:
#: every burn within 0.028 of its lap flew its price to within ten per cent;
#: at 0.030 the hull fell off the arc while it burnt and paid 2.7 times it,
#: and from a twentieth on the fastest arcs -- half a radius over the ground
#: -- took some hulls through it. A fortieth keeps a margin under the first
#: failure. A point whose burns are longer is not offered: the engines cannot
#: fly it as priced.
IMPULSE_SHARE = 1.0 / 40

#: How far the other worlds may move a hull along the arc's orbit over the
#: flight, as a share of the orbit's far radius (`bound.tide_slip`): past it
#: the arc round the planet alone is a fiction the helm pays to correct.
#: Measured 2026-09-19 under the whole sky, half a lap behind: round Pyroxis,
#: where the star's tide is twenty-six times Terra's, a flight within a fifth
#: flew its price to fifteen per cent, a quarter cost 1.4 times it, two fifths
#: twice, and a hundred hours ten times; Terra's whole slider, to twelve
#: days, is within a seventh.
TIDE_SHARE = 0.2

#: The steps of a correction (`corrected`), and the nudge of velocity its
#: derivative is taken over, as a share of the hull's own speed round the
#: planet. A miss worth correcting is a small one, and the coast's end moves
#: with the velocity near enough in a straight line: two or three steps
#: close it as far as it closes.
_NEWTON = 4
_NUDGE = 1e-7
#: The directions a correction leaves alone, by how little the coast's end
#: moves along them against the direction it moves most. Over many laps the
#: end moves almost only along the orbit -- any change of speed is a change
#: of the lap's length -- and the across-the-orbit part of a miss would take
#: a burn thirty thousand times the size to close, which Newton then took
#: and flew off every orbit that keeps (measured 2026-09-19, eight days round
#: Terra). That part is left for later in the lap, when it is cheap.
_RCOND = 1e-2


@dataclass(frozen=True, slots=True)
class Arc:
    """The cheapest arc round a planet to where another hull will be."""

    #: The velocity to leave on, heliocentric.
    v1: tuple[float, float]
    #: The speed changed leaving, and matching the other hull at the end.
    dv_out: float
    dv_in: float
    #: Full laps round the planet before the meeting.
    revs: int
    #: Where the hull starts and how it leaves, relative to the planet, and
    #: the time one lap of the arc's orbit takes: what the chart draws.
    rel: tuple[float, float]
    v_rel: tuple[float, float]
    period: float
    #: The far point of the arc's orbit from the planet's centre, map units.
    far: float


def shared_world(system: System, t: float, r: tuple[float, float], target: Drifter) -> Body | None:
    """The planet a meeting with `target` is flown round: the one it is in
    orbit round (`bound.Orbiter`), if the hull is inside that planet's inner
    sphere at `t` -- where the other worlds pull only as a tide. Nothing
    otherwise, and then the approach is the straight profile: a hull coasting
    away from a planet is met on its line, not on an orbit it is not on."""
    if not isinstance(target, Orbiter):
        return None
    body = target.held.body
    p, _ = place(body, t)
    mine = float(np.hypot(r[0] - p[0, 0], r[1] - p[0, 1]))
    return body if mine < INNER_SHARE * hill_of(system, body) else None


def arc_to(
    system: System,
    body: Body,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
    target: Drifter,
    tof: float,
) -> Arc | None:
    """The cheapest arc round `body` from the hull at `t` to where `target`
    will be `tof` days later: the least speed changed at both ends together,
    over the laps near the count the hull's own lap puts in the hours, both
    ways round. Only an arc that is itself an orbit that keeps; nothing if
    none is, and nothing where the two places are on one ray from the planet
    (`SAME_RAY`).

    Both ends in the sum, and not the departure alone: a hull already on the
    arc it was ordered along leaves on it for nothing, so the arc it flies
    stays the cheapest at every step after the first, and the helm keeps to
    it rather than hopping to an arc of another lap count."""
    if tof <= 0.0:
        return None
    p, vp = place(body, t)
    rel = (float(r[0] - p[0, 0]), float(r[1] - p[0, 1]))
    v_rel = (float(v[0] - vp[0, 0]), float(v[1] - vp[0, 1]))
    q, vq = place_any(target, t + tof)
    pe, vpe = place(body, t + tof)
    goal = (float(q[0, 0] - pe[0, 0]), float(q[0, 1] - pe[0, 1]))
    v_goal = (float(vq[0, 0] - vpe[0, 0]), float(vq[0, 1] - vpe[0, 1]))
    turn = abs(np.arctan2(astro.cross(rel, goal), astro.dot(rel, goal)))
    if turn < SAME_RAY:
        return None
    centre = int(tof / _lap_of(body, rel, v_rel))
    best: Arc | None = None
    for revs in range(max(0, centre - LAP_WINDOW), centre + LAP_WINDOW + 1):
        for retrograde in (False, True):
            for v1, v2 in astro.lambert(body.mu, rel, goal, tof, revs, retrograde=retrograde):
                kept = closed_orbit(system, body, rel, v1)
                if kept is None:
                    continue
                dv_out = astro.norm(astro.sub(v1, v_rel))
                dv_in = astro.norm(astro.sub(v_goal, v2))
                if best is None or dv_out + dv_in < best.dv_out + best.dv_in:
                    best = Arc(
                        v1=(v1[0] + float(vp[0, 0]), v1[1] + float(vp[0, 1])),
                        dv_out=dv_out,
                        dv_in=dv_in,
                        revs=revs,
                        rel=rel,
                        v_rel=v1,
                        period=astro.lap(body.mu, kept[0]),
                        far=kept[1],
                    )
    return best


def corrected(
    system: System,
    body: Body,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
    target: Drifter,
    tof: float,
) -> Arc | None:
    """The arc nearest the one the hull is flying that comes closer to where
    `target` will be `tof` days on: Gauss and Newton on the velocity, from the
    hull's own, through the two-body coast round `body` -- a correction, not
    a new arc, and as much of the miss as is cheap to close now (`_RCOND`).

    What `arc_to` cannot promise to find. Lambert's roots of many laps lie in
    narrow places, and a lost one sent a hull forty laps into a meeting onto
    an arc of another lap count for a unit of speed where a hundredth would
    have done (measured 2026-09-19 round Terra, a meeting of eight days).
    Starting from where the hull already goes, it stays on its own arc.
    Nothing if no step brings the end closer on an orbit that keeps."""
    if tof <= 0.0:
        return None
    p, vp = place(body, t)
    rel = (float(r[0] - p[0, 0]), float(r[1] - p[0, 1]))
    v_rel = np.array([v[0] - vp[0, 0], v[1] - vp[0, 1]], dtype=float)
    q, vq = place_any(target, t + tof)
    pe, vpe = place(body, t + tof)
    goal = np.array([q[0, 0] - pe[0, 0], q[0, 1] - pe[0, 1]])
    v_goal = np.array([vq[0, 0] - vpe[0, 0], vq[0, 1] - vpe[0, 1]])
    nudge = _NUDGE * (float(np.hypot(*v_rel)) or 1.0)
    u = v_rel.copy()

    def end_of(speed: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        there, going = astro.propagate(body.mu, rel, (float(speed[0]), float(speed[1])), tof)
        return np.array(there), np.array(going)

    there, going = end_of(u)
    moved = False
    for _ in range(_NEWTON):
        miss = goal - there
        if float(np.hypot(*miss)) <= AIM_SHARE * system.dock_radius / 2:
            break
        columns = [(end_of(u + nudge * axis)[0] - there) / nudge for axis in np.eye(2)]
        step = np.linalg.lstsq(np.column_stack(columns), miss, rcond=_RCOND)[0]
        trial = u + step
        #: A step off every orbit that keeps is no correction: a miss that
        #: large is a new arc's to close (`arc_to`), and the coast of an open
        #: orbit over days overflows the arithmetic besides.
        if closed_orbit(system, body, rel, (float(trial[0]), float(trial[1]))) is None:
            break
        near, near_going = end_of(trial)
        if float(np.hypot(*(goal - near))) >= float(np.hypot(*miss)):
            break
        u, there, going, moved = trial, near, near_going, True
    if not moved:
        return None
    start = (float(u[0]), float(u[1]))
    kept = closed_orbit(system, body, rel, start)
    if kept is None:
        return None
    return Arc(
        v1=(start[0] + float(vp[0, 0]), start[1] + float(vp[0, 1])),
        dv_out=float(np.hypot(*(u - v_rel))),
        dv_in=float(np.hypot(*(v_goal - going))),
        revs=int(tof / astro.lap(body.mu, kept[0])),
        rel=rel,
        v_rel=start,
        period=astro.lap(body.mu, kept[0]),
        far=kept[1],
    )


def coast_end(
    body: Body,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
    target: Drifter,
    tof: float,
) -> tuple[float, float]:
    """Where the hull's coast ends against `target` `tof` days on, if it burns
    nothing: how far from it, and the speed it would take to match it there.
    Its own coast round `body`, two-body, as an arc is laid."""
    p, vp = place(body, t)
    rel = (float(r[0] - p[0, 0]), float(r[1] - p[0, 1]))
    v_rel = (float(v[0] - vp[0, 0]), float(v[1] - vp[0, 1]))
    there, going = astro.propagate(body.mu, rel, v_rel, tof)
    q, vq = place_any(target, t + tof)
    pe, vpe = place(body, t + tof)
    miss = float(np.hypot(there[0] + pe[0, 0] - q[0, 0], there[1] + pe[0, 1] - q[0, 1]))
    match = float(np.hypot(going[0] + vpe[0, 0] - vq[0, 0], going[1] + vpe[0, 1] - vq[0, 1]))
    return miss, match


def flown_as_priced(system: System, body: Body, found: Arc, tof: float, a_max: float) -> bool:
    """Whether this arc is flown as it is priced: each of its two burns an
    instant for engines of `a_max` -- within `IMPULSE_SHARE` of a lap of its
    orbit -- and the arc the sky's own for the `tof` it takes.

    The second is the tide's (`TIDE_SHARE`): over the flight the other
    worlds may move the hull along its orbit by no more than a fifth of its
    far radius."""
    if a_max <= 0.0:
        return False
    if max(found.dv_out, found.dv_in) / a_max > IMPULSE_SHARE * found.period:
        return False
    return tide_slip(system, body, found.far, found.period, tof) <= TIDE_SHARE * found.far


def _lap_of(body: Body, rel: tuple[float, float], v_rel: tuple[float, float]) -> float:
    """The time the hull's own orbit round `body` takes a lap: its ellipse's,
    or -- on no closed orbit, coming in -- the circle's at the distance it is."""
    gap = astro.norm(rel)
    energy = astro.norm(v_rel) ** 2 / 2 - body.mu / gap
    axis = -body.mu / (2 * energy) if energy < 0 else gap
    return astro.lap(body.mu, axis)
