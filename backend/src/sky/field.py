# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The pull of the five bodies, and the integrator that flies a batch of
hulls through it (D-289).

Runge-Kutta of the fourth order, with **every row on its own clock**: a
batch is forty passages that leave together and arrive weeks apart, and a
hull skimming a planet needs steps of minutes where one in the deep needs
hours. So each row carries its own time and its own step, and a step is
shortened for the row alone -- a whole batch crawling because one of its
rows is near Terra would make the slider unusable.

The step is bounded by the orbital time scale of the nearest body:
`sqrt(d^3 / mu)` is the period over two pi, and a twentieth of it keeps
Runge-Kutta honest on a circle (the test pins the energy drift).

## Near a planet the other worlds pull as a tide

The planets ride the circles the seed laid and do not feel one another; a
hull feels all five bodies. Far out that is the whole truth -- the star
stands still and the planets are where they are. Near a planet it is not:
Pyroxis pulls a hull circling Terra and does not pull Terra, and that nearly
steady outside push swings the circle into an ellipse that meets the ground
in four to twelve days by where Pyroxis stands, and in more than two weeks
at a few of its places (measured 2026-09-18, D-354). In the world the planet
would fall toward Pyroxis together with the hull, and only the difference of
the two pulls -- the tide -- would be left between them.

So inside a planet's inner sphere every other world's pull on the hull is
taken less its pull on that planet, exactly what the planet would feel if it
were free; from the sphere's edge to the Hill radius the correction fades
smoothly, and beyond it the hull feels the full pulls again, which is what
an interplanetary arc must feel. The star needs no such care: it is the
still centre the circles are laid round, and the circles obey it.
"""

from __future__ import annotations

import functools
from collections.abc import Callable

import numpy as np

from src.sky._base import (
    INNER_SHARE,
    Body,
    Rows,
    System,
    angle_of,
    hill_of,
    norms,
    place,
    turned,
)

#: How much of the nearest body's orbital time scale one step may take.
STEP_SHARE = 0.05
#: Steps never shorter than this, days: a hull inside a planet's radius is
#: lost anyway, and the integrator must not stall on it.
STEP_FLOOR = 1e-4
#: Rows below this length are treated as at the centre, so that a division by
#: nothing never reaches the arithmetic.
_TINY = 1e-9


def pull(system: System, t: np.ndarray, r: Rows) -> Rows:
    """The acceleration of every row at its own time: the star and the planets,
    the other worlds as a tide inside a planet's inner sphere.

    The planets are taken all at once, `(bodies, rows)`, and the plane as
    complex numbers: the integrator asks this four times a step, and on the
    small batches it flies the count of array operations is what a step
    costs. Measured against the pull before the tide: one row near a planet
    costs the same, forty rows near one about two fifths more, and a row in
    the deep a third less.
    """
    z = r[:, 0] + 1j * r[:, 1]
    square = np.maximum(z.real * z.real + z.imag * z.imag, _TINY * _TINY)
    accel = -system.mu / (square * np.sqrt(square)) * z
    if not system.bodies:
        return np.stack([accel.real, accel.imag], axis=1)
    mu, hill, radius, phase, period = _columns(system)
    when = np.broadcast_to(np.asarray(t, dtype=float), z.shape)
    centre = radius * np.exp(1j * turned(phase, period, when))
    off = z - centre
    square = np.maximum(off.real * off.real + off.imag * off.imag, _TINY * _TINY)
    accel -= np.sum(mu / (square * np.sqrt(square)) * off, axis=0)
    #: The tide: each other world's pull on the host planet given back, as
    #: much as the row is in that planet's inner sphere. The Hill spheres of
    #: two worlds never overlap (their circles lie far wider apart than their
    #: Hill radii), so a row has one host at most; a row in none gets nought.
    inside = square < hill * hill
    if len(system.bodies) < 2 or not inside.any():
        return np.stack([accel.real, accel.imag], axis=1)
    rows = np.arange(z.shape[0])
    host = np.argmax(inside, axis=0)
    outer = hill[host, 0]
    u = np.clip((outer - np.sqrt(square[host, rows])) / ((1.0 - INNER_SHARE) * outer), 0.0, 1.0)
    share = np.where(inside[host, rows], u * u * (3.0 - 2.0 * u), 0.0)
    apart = centre[host, rows] - centre
    gap = np.maximum(apart.real * apart.real + apart.imag * apart.imag, _TINY * _TINY)
    weight = mu / (gap * np.sqrt(gap))
    #: A world does not pull itself: the host's own entry is nought.
    weight[host, rows] = 0.0
    accel += share * np.sum(weight * apart, axis=0)
    return np.stack([accel.real, accel.imag], axis=1)


@functools.lru_cache(maxsize=8)
def _columns(system: System) -> tuple[np.ndarray, ...]:
    """Each planet's pull, Hill radius, circle's radius, phase and year as a
    column, `(bodies, 1)`: read once per system rather than four times a step."""
    columns = tuple(
        np.array([[value] for value in values], dtype=float)
        for values in (
            [body.mu for body in system.bodies],
            [hill_of(system, body) for body in system.bodies],
            [body.orbit[0] for body in system.bodies],
            [body.orbit[2] for body in system.bodies],
            [body.orbit[1] for body in system.bodies],
        )
    )
    for column in columns:
        column.setflags(write=False)
    return columns


def time_scale(system: System, t: np.ndarray, r: Rows) -> np.ndarray:
    """The shortest orbital time scale a row sees, days: the star's or the
    nearest planet's, whichever pulls it round faster."""
    x, y = r[:, 0], r[:, 1]
    square = np.maximum(x * x + y * y, _TINY * _TINY)
    scale = square * np.sqrt(square) / system.mu
    for body in system.bodies:
        dx, dy = _offset(body, t, x, y)
        square = np.maximum(dx * dx + dy * dy, _TINY * _TINY)
        scale = np.minimum(scale, square * np.sqrt(square) / body.mu)
    return np.sqrt(scale)


def _offset(
    body: Body, t: np.ndarray, x: np.ndarray, y: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Each row's offset from a planet at its own time: `place` without the
    velocity and without the stacking -- the integrator asks this four times a
    step per planet, and it was most of what a step cost."""
    angle = angle_of(body, t)
    return x - body.orbit[0] * np.cos(angle), y - body.orbit[0] * np.sin(angle)


def _rk4(
    system: System, t: np.ndarray, r: Rows, v: Rows, dt: np.ndarray, thrust: Rows | None
) -> tuple[Rows, Rows]:
    """One Runge-Kutta step per row, each with its own `dt` (shape `(N,)`).

    `thrust` is an acceleration held constant over the step, per row -- the
    engines' contribution while they burn, nothing while they coast.
    """
    h = dt[:, None]
    extra = 0.0 if thrust is None else thrust

    def accel(tt: np.ndarray, rr: Rows) -> Rows:
        return pull(system, tt, rr) + extra

    k1v = accel(t, r)
    k1r = v
    k2v = accel(t + dt / 2, r + h / 2 * k1r)
    k2r = v + h / 2 * k1v
    k3v = accel(t + dt / 2, r + h / 2 * k2r)
    k3r = v + h / 2 * k2v
    k4v = accel(t + dt, r + h * k3r)
    k4r = v + h * k3v
    r_next = r + h / 6 * (k1r + 2 * k2r + 2 * k3r + k4r)
    v_next = v + h / 6 * (k1v + 2 * k2v + 2 * k3v + k4v)
    return r_next, v_next


def advance(
    system: System,
    t: np.ndarray,
    until: np.ndarray,
    r: Rows,
    v: Rows,
    *,
    dt_max: float,
    thrust: Rows | None = None,
    watch: Callable[[np.ndarray, Rows, Rows], np.ndarray | None] | None = None,
) -> tuple[Rows, Rows]:
    """Fly every row from its own `t` to its own `until`, and return the states there.

    Each row flies toward its own `until` and stands still once there; the
    loop runs until the last one is home. A row whose `until` lies **before**
    its `t` is flown backwards in time --
    the flyby's plan integrates out of a periapsis both ways (`sky.shoot`), and
    the same steps taken with a negative sign are the same arithmetic.
    `watch` is called after every step with the times and the states -- the
    forecast looks for the ground through it, the sampler for its moments. It
    may answer with a mask of rows to halt where they are: a row the planner
    has already found in the corona is not worth the hundred shrinking steps
    the star's pull would ask of it.
    """
    t = np.array(t, dtype=float)
    until = np.array(until, dtype=float)
    r = np.array(r, dtype=float)
    v = np.array(v, dtype=float)
    sign = np.where(until < t, -1.0, 1.0)
    while True:
        left = (until - t) * sign
        active = left > 0
        if not np.any(active):
            return r, v
        dt = np.minimum(dt_max, STEP_SHARE * time_scale(system, t, r))
        dt = np.maximum(dt, STEP_FLOOR)
        dt = np.where(active, np.minimum(dt, np.maximum(left, 0.0)), 0.0) * sign
        r_next, v_next = _rk4(system, t, r, v, dt, thrust)
        moved = active[:, None]
        r = np.where(moved, r_next, r)
        v = np.where(moved, v_next, v)
        t = t + dt
        if watch is not None:
            halt = watch(t, r, v)
            if halt is not None:
                until = np.where(halt, t, until)


def sample(
    system: System,
    t0: float,
    r: Rows,
    v: Rows,
    spans: np.ndarray,
    *,
    dt_max: float,
    points: int,
) -> np.ndarray:
    """Each row's path as `points` positions at equal time steps over its own
    `span` (days), both ends included: what the chart draws (D-271, D-289).

    Shape `(N, points, 2)`. The rows are flown to every sample moment in turn,
    so a row with a long span keeps flying while a short one waits.
    """
    n = r.shape[0]
    out = np.empty((n, points, 2), dtype=float)
    out[:, 0, :] = r
    t = np.full(n, float(t0))
    cur_r, cur_v = np.array(r, dtype=float), np.array(v, dtype=float)
    spans = np.asarray(spans, dtype=float)
    for i in range(1, points):
        until = t0 + spans * i / (points - 1)
        cur_r, cur_v = advance(system, t, until, cur_r, cur_v, dt_max=dt_max)
        t = np.maximum(t, until)
        out[:, i, :] = cur_r
    return out


def nearest_body(system: System, t: float, r: Rows) -> tuple[Body | None, np.ndarray]:
    """The planet closest to each row and the distance to it (`(N,)`)."""
    best: Body | None = None
    gap = np.full(r.shape[0], np.inf)
    for body in system.bodies:
        p, _ = place(body, t)
        d = norms(r - p)
        closer = d < gap
        if np.any(closer):
            gap = np.where(closer, d, gap)
            best = body if best is None or bool(closer.all()) else best
    return best, gap
