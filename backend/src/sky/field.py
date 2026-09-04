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
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from src.sky._base import Body, Rows, System, norms, place

#: How much of the nearest body's orbital time scale one step may take.
STEP_SHARE = 0.05
#: Steps never shorter than this, days: a hull inside a planet's radius is
#: lost anyway, and the integrator must not stall on it.
STEP_FLOOR = 1e-4
#: Rows below this length are treated as at the centre, so that a division by
#: nothing never reaches the arithmetic.
_TINY = 1e-9


def pull(system: System, t: np.ndarray, r: Rows) -> Rows:
    """The acceleration of every row at its own time: the star and the planets."""
    distance = np.maximum(norms(r), _TINY)[:, None]
    a = -system.mu * r / distance**3
    for body in system.bodies:
        p, _ = place(body, t)
        d = r - p
        gap = np.maximum(norms(d), _TINY)[:, None]
        a = a - body.mu * d / gap**3
    return a


def time_scale(system: System, t: np.ndarray, r: Rows) -> np.ndarray:
    """The shortest orbital time scale a row sees, days: the star's or the
    nearest planet's, whichever pulls it round faster."""
    distance = np.maximum(norms(r), _TINY)
    scale = np.sqrt(distance**3 / system.mu)
    for body in system.bodies:
        p, _ = place(body, t)
        gap = np.maximum(norms(r - p), _TINY)
        scale = np.minimum(scale, np.sqrt(gap**3 / body.mu))
    return scale


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
    watch: Callable[[np.ndarray, Rows, Rows], None] | None = None,
) -> tuple[Rows, Rows]:
    """Fly every row from its own `t` to its own `until`, and return the states there.

    A row past its end stands still; the loop runs until the last one is home.
    `watch` is called after every step with the times and the states -- the
    forecast looks for the ground through it, the sampler for its moments.
    """
    t = np.array(t, dtype=float)
    until = np.asarray(until, dtype=float)
    r = np.array(r, dtype=float)
    v = np.array(v, dtype=float)
    while True:
        left = until - t
        active = left > 0
        if not np.any(active):
            return r, v
        dt = np.minimum(dt_max, STEP_SHARE * time_scale(system, t, r))
        dt = np.maximum(dt, STEP_FLOOR)
        dt = np.where(active, np.minimum(dt, np.maximum(left, 0.0)), 0.0)
        r_next, v_next = _rk4(system, t, r, v, dt, thrust)
        moved = active[:, None]
        r = np.where(moved, r_next, r)
        v = np.where(moved, v_next, v)
        t = t + dt
        if watch is not None:
            watch(t, r, v)


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
