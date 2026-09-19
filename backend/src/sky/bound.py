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
from dataclasses import dataclass

import numpy as np

from src import astro
from src.sky._base import GROUND_MARGIN, STABLE_SHARE, Body, Rows, System, hill_of, place

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
    return Bound(body=body, t0=t0, rel=rel, v_rel=v_rel, period=astro.lap(body.mu, axis), far=far)


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
    hill = hill_of(system, held.body)
    if hill <= 0.0 or held.period <= 0.0:
        return False
    tide = (held.far / hill) ** 3
    slip = _KEPLER_UNDERCOUNT * tide * 2 * np.pi * window / held.period * held.far
    return slip < KEPLER_SLACK * system.dock_radius


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
