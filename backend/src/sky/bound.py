# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A hull bound to a planet (D-354): the closed orbit it keeps, and where
on it the hull is at any moment -- without a single integrator step.

A hull that burns nothing on a closed orbit reaching no further than a fifth
of a planet's Hill radius (`STABLE_SHARE`) and dipping no lower than half a
radius over its ground (`GROUND_MARGIN`) is **bound** to it: the orbit
neither meets the ground nor leaves -- measured, not assumed -- and the
console calls it "in orbit". Where on
it the hull is can then be **read** by Kepler round that planet, the exact
two-body solution -- the way the parking circle used to be arithmetic, only
for any closed orbit rather than one.

A reading, not the hull's motion. The tick still moves the stamp every
`orbit.restamp_hours` under the whole sky (`sky.field`), and every order,
hold and loss writes its stamp from the whole sky too (`exact` in
`engine.ship.sim.state_at`): what the star and the other worlds do to an
orbit over weeks is the integrator's to say. And only where what Kepler
leaves out is small (`kepler_reads`): over one restamp the reading must
stay within a hundredth of the distance two hulls meet at
(`orbit.dock_radius`). The parking circles of Terra, Aquatica and Aurora
are well inside that; Pyroxis' is not -- Pyroxis is close to the star, and
its circle swings by a quarter of a per cent under the star's tide -- so a
hull there is read by the integrator, as a drifter is.

It is a reading worth having because hulls at a planet are the many and
the still: the sighting and the console place every one of them each tick,
and a restamp's worth of integrator steps apiece near a planet -- where the
steps are shortest -- was the dearest thing such a reading could cost.

The propagation is batched and solves Kepler's equation in the change of
the eccentric anomaly, which needs no eccentricity to divide by: a circle
is as ordinary a case as any ellipse.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from src import astro
from src.sky._base import (
    GROUND_MARGIN,
    STABLE_SHARE,
    Body,
    Drifter,
    Rows,
    System,
    hill_of,
    place,
)

#: Newton's steps on Kepler's equation. From half a turn the iteration
#: converges for every ellipse (Charles and Tatum), to the last digit in
#: under a dozen steps even at an eccentricity of 0.99 -- the test pins it;
#: a fixed count keeps the batch in lockstep.
_NEWTON = 16

#: How far a Kepler reading may stray from the whole sky over one restamp,
#: as a share of the distance two hulls meet at (`orbit.dock_radius`).
KEPLER_SLACK = 0.01

#: By how much the slip estimate of `kepler_reads` falls short of the true
#: one at worst (measured 2026-09-19: 2.25 at Terra, 1.54 at Aquatica, over
#: eight orientations and eight epochs), rounded up.
_KEPLER_UNDERCOUNT = 3.0


@dataclass(frozen=True, slots=True)
class Bound:
    """A closed orbit round one planet, as it stood at `t0`: the hull's place
    and speed relative to the planet, and the time one lap takes."""

    body: Body
    t0: float
    rel: tuple[float, float]
    v_rel: tuple[float, float]
    period: float
    #: The far point's distance from the planet, map units.
    far: float


def bound_to(
    system: System, t0: float, r0: tuple[float, float], v0: tuple[float, float]
) -> Bound | None:
    """The closed orbit round the nearest planet the hull is on, if it keeps
    one: negative two-body energy, the near point `GROUND_MARGIN` radii or
    more from the planet's centre, the far point within `STABLE_SHARE` of its
    Hill radius. Nothing otherwise -- and then the coast is flown, not taken
    on trust."""
    if not system.bodies or system.mu <= 0:
        return None
    body, rel, v_rel = nearest(system, t0, r0, v0)
    kept = closed_orbit(system, body, rel, v_rel)
    if kept is None:
        return None
    axis, far = kept
    return Bound(body=body, t0=t0, rel=rel, v_rel=v_rel, period=astro.lap(body.mu, axis), far=far)


def closed_orbit(
    system: System, body: Body, rel: tuple[float, float], v_rel: tuple[float, float]
) -> tuple[float, float] | None:
    """The semi-major axis and the far point of the orbit round `body` that a
    hull at `rel`, moving at `v_rel` relative to the planet, is on -- if it is
    one that keeps: closed, its near point `GROUND_MARGIN` radii or more from
    the centre, its far point within `STABLE_SHARE` of the Hill radius.
    Nothing otherwise. What `bound_to` asks of a hull, and a meeting in orbit
    of every arc it would fly (`sky.rendezvous`)."""
    gap = astro.norm(rel)
    if gap <= 0:
        return None
    speed = astro.norm(v_rel)
    energy = speed * speed / 2 - body.mu / gap
    if energy >= 0:
        return None
    axis = -body.mu / (2 * energy)
    momentum = astro.cross(rel, v_rel)
    excess = 1 + 2 * energy * momentum * momentum / (body.mu * body.mu)
    eccentricity = float(np.sqrt(max(0.0, excess)))
    if axis * (1 - eccentricity) < GROUND_MARGIN * body.radius:
        return None
    far = axis * (1 + eccentricity)
    if far >= hill_of(system, body) * STABLE_SHARE:
        return None
    return axis, far


@dataclass(frozen=True, slots=True)
class Orbiter(Drifter):
    """A hull in orbit as a target (D-354, wave 3): a drifter whose line is
    its orbit, read by Kepler round its planet from the stamp it was read at.

    Not its line of points. The forecast is a lap of two dozen of them and the
    helm's own line of the target a point an hour; round Terra a lap is three
    and a half hours, so an hourly line is chords across a quarter of the
    orbit, and a target read off it was up to a third of the orbit away from
    where it was. Kepler is the sky here to within a hundredth of the meeting
    distance over a restamp where `kepler_reads` says so; where it does not --
    Pyroxis -- it is still an aim, re-solved every step from where both hulls
    are, and the hold is stamped from the whole sky (`hold.begin`).
    """

    held: Bound = field(kw_only=True)

    def state(self, t: np.ndarray | float) -> tuple[Rows, Rows]:
        tt = np.atleast_1d(np.asarray(t, dtype=float))
        n = len(tt)
        held = self.held
        r, v = kepler(
            np.full(n, held.body.mu),
            np.tile(np.asarray(held.rel, dtype=float), (n, 1)),
            np.tile(np.asarray(held.v_rel, dtype=float), (n, 1)),
            tt - held.t0,
        )
        p, vp = place(held.body, tt)
        return r + p, v + vp


def orbiter(key: str, held: Bound) -> Orbiter:
    """A hull on this orbit as a target: a lap without end, round its planet."""
    return Orbiter(
        key=key,
        t0=held.t0,
        t1=held.t0 + held.period,
        trace=(),
        loops=True,
        around=held.body,
        held=held,
    )


def kepler_reads(system: System, held: Bound, window: float) -> bool:
    """Whether Kepler may read this orbit for `window` days: whether what it
    leaves out strays the hull by under `KEPLER_SLACK` of the meeting distance.

    The star's tide at the far point is `(far / Hill)^3` of the planet's own
    pull; over the window it turns into a slip along the orbit of about that
    share of the laps flown, at the far point's radius. That is the star's
    part only: the other worlds' tides add to it -- Pyroxis in conjunction by
    two fifths at Terra -- and measured over every orientation and epoch the
    true slip came to as much as `_KEPLER_UNDERCOUNT` times the estimate, so
    the estimate is taken that many times over.
    """
    slip = tide_slip(system, held.body, held.far, held.period, window)
    return slip < KEPLER_SLACK * system.dock_radius


def tide_slip(system: System, body: Body, far: float, period: float, window: float) -> float:
    """How far along an orbit round `body` the other worlds move a hull over
    `window` days, against Kepler round the planet alone: the star's tide at
    the far point, `(far / Hill)^3` of the planet's own pull, turned into a
    slip of that share of the laps flown at the far point's radius -- taken
    `_KEPLER_UNDERCOUNT` times over, for the other worlds' part. Infinite
    where there is no orbit to speak of."""
    hill = hill_of(system, body)
    if hill <= 0.0 or period <= 0.0:
        return float("inf")
    tide = (far / hill) ** 3
    return _KEPLER_UNDERCOUNT * tide * 2 * np.pi * window / period * far


def nearest(
    system: System, t0: float, r0: tuple[float, float], v0: tuple[float, float]
) -> tuple[Body, tuple[float, float], tuple[float, float]]:
    """The planet the hull is closest to, and the hull's state relative to it."""
    best: tuple[Body, tuple[float, float], tuple[float, float]] | None = None
    for body in system.bodies:
        p, vp = place(body, t0)
        rel = (float(r0[0] - p[0, 0]), float(r0[1] - p[0, 1]))
        if best is None or astro.norm(rel) < astro.norm(best[1]):
            best = (body, rel, (float(v0[0] - vp[0, 0]), float(v0[1] - vp[0, 1])))
    assert best is not None
    return best


def bound_states(bounds: Sequence[Bound], t: np.ndarray | float) -> tuple[Rows, Rows]:
    """Where each bound hull is at `t` (one moment for all, or one per hull),
    and how it moves: its planet's place and speed plus its own on the orbit.
    Heliocentric rows, one per hull."""
    n = len(bounds)
    when = np.broadcast_to(np.asarray(t, dtype=float), (n,))
    mu = np.array([one.body.mu for one in bounds], dtype=float)
    rel = np.array([one.rel for one in bounds], dtype=float).reshape(n, 2)
    v_rel = np.array([one.v_rel for one in bounds], dtype=float).reshape(n, 2)
    dt = when - np.array([one.t0 for one in bounds], dtype=float)
    r, v = kepler(mu, rel, v_rel, dt)
    for i, one in enumerate(bounds):
        p, vp = place(one.body, when[i])
        r[i] += p[0]
        v[i] += vp[0]
    return r, v


def rounded(
    body: Body, t: float, r: tuple[float, float], v: tuple[float, float]
) -> tuple[tuple[float, float], float]:
    """The last burn of an arrival (D-354): the velocity of the circle round
    `body` through the hull's own place, the way the hull already goes round,
    and the speed that burn costs. The circle at the height the hull is at,
    not at the parking radius: the burn changes how the hull moves, never
    where it is."""
    p, vp = place(body, t)
    rel = np.array(r, dtype=float) - p[0]
    v_rel = np.array(v, dtype=float) - vp[0]
    gap = float(np.hypot(*rel))
    sense = 1.0 if astro.cross((rel[0], rel[1]), (v_rel[0], v_rel[1])) >= 0 else -1.0
    want = np.array([-rel[1], rel[0]]) / gap * sense * float(np.sqrt(body.mu / gap))
    new = vp[0] + want
    return (float(new[0]), float(new[1])), float(np.hypot(*(want - v_rel)))


def kepler(mu: np.ndarray, r0: Rows, v0: Rows, dt: np.ndarray) -> tuple[Rows, Rows]:
    """Each row `dt` along its closed orbit round a centre of pull `mu`.

    Written in the change of eccentric anomaly `dE` (Battin's f and g), so
    the circle needs no eccentricity to divide by: `e sin E0` and
    `e cos E0` come straight from the state -- `r0 . v0 / sqrt(mu a)` and
    `1 - r0 / a` -- and Kepler's own equation is solved for the anomaly
    itself, by Newton from half a turn, which converges for every ellipse.
    Whole laps are taken off first: they bring the hull back where it was,
    and a month of them would only feed the arithmetic its own rounding.
    """
    n0 = np.sqrt(np.sum(r0 * r0, axis=1))
    speed2 = np.sum(v0 * v0, axis=1)
    axis = 1.0 / (2.0 / n0 - speed2 / mu)
    motion = np.sqrt(mu / axis**3)
    dt = np.mod(dt, 2 * np.pi / motion)
    #: Where on the ellipse the row starts: `e sin E0` and `e cos E0`.
    rise = np.sum(r0 * v0, axis=1) / np.sqrt(mu * axis)
    lean = 1.0 - n0 / axis
    eccentricity = np.hypot(rise, lean)
    start = np.arctan2(rise, lean)
    mean = start - rise + motion * dt
    turns = np.floor(mean / (2 * np.pi)) * 2 * np.pi
    within = mean - turns
    anomaly = np.full_like(within, np.pi)
    for _ in range(_NEWTON):
        miss = anomaly - eccentricity * np.sin(anomaly) - within
        anomaly = anomaly - miss / (1.0 - eccentricity * np.cos(anomaly))
    d_e = anomaly + turns - start
    cos_e, sin_e = np.cos(d_e), np.sin(d_e)
    f = 1.0 - axis / n0 * (1.0 - cos_e)
    g = dt - (d_e - sin_e) / motion
    r = f[:, None] * r0 + g[:, None] * v0
    n = np.sqrt(np.sum(r * r, axis=1))
    f_dot = -np.sqrt(mu * axis) * sin_e / (n * n0)
    g_dot = 1.0 - axis / n * (1.0 - cos_e)
    v = f_dot[:, None] * r0 + g_dot[:, None] * v0
    return r, v
