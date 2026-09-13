# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Lambert's problem for a batch of rows at once, zero turns only (D-341).

`astro.lambert` answers one question at a time, and the flyby's search asks
tens of thousands: every moment of the pass against every flight time, both
ways round, for every world that could lend its pull. So the same
universal-variable arithmetic (Curtis alg. 5.2) is written here over arrays:
one bisection for all rows, stepped together. Zero turns only -- a flyby's legs
are the short ways between two worlds, and on the zero-turn branch the time
of flight grows with `z` monotonically, so one bracket and one bisection find
the one root each row has.

A row with no arc -- no bracket, a degenerate geometry -- comes back `ok`
False and NaN velocities, so the caller masks rather than branches.
"""

from __future__ import annotations

import math

import numpy as np

from src.sky._base import Rows

#: How far from a half-turn and from nothing the transfer angle is nudged, as
#: in `astro.lambert`: at those angles the plane of the arc is undefined.
_HALF_TURN_EPS = 1e-4
#: The top of the zero-turn bracket stops this short of a full turn.
_EDGE = 1e-6
#: How often the bracket's floor may be doubled down for a steep hyperbola.
_DOUBLINGS = 20
#: Bisection steps: the bracket can be `2^20 * 4 pi^2` wide, and sixty-four
#: halvings take it well under the arithmetic's own last digit.
_STEPS = 64
#: Near nought the Stumpff functions are their limits.
_SMALL = 1e-9
_TINY = 1e-12


def _stumpff(z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """C(z) and S(z) for every row, each branch fed only its own rows."""
    pos = z > _SMALL
    neg = z < -_SMALL
    root_p = np.sqrt(np.where(pos, z, 1.0))
    root_n = np.sqrt(np.where(neg, -z, 1.0))
    c = np.where(
        pos,
        (1 - np.cos(root_p)) / np.where(pos, z, 1.0),
        np.where(neg, (np.cosh(root_n) - 1) / np.where(neg, -z, 1.0), 0.5),
    )
    s = np.where(
        pos,
        (root_p - np.sin(root_p)) / root_p**3,
        np.where(neg, (np.sinh(root_n) - root_n) / root_n**3, 1.0 / 6.0),
    )
    return c, s


def arcs(
    mu: float, r1: Rows, r2: Rows, tof: np.ndarray, retrograde: np.ndarray
) -> tuple[Rows, Rows, np.ndarray]:
    """The zero-turn arc of every row: departure and arrival velocities, and
    whether the row has one."""
    r1 = np.asarray(r1, dtype=float)
    r2 = np.asarray(r2, dtype=float)
    tof = np.asarray(tof, dtype=float)
    retrograde = np.asarray(retrograde, dtype=bool)
    n1 = np.hypot(r1[:, 0], r1[:, 1])
    n2 = np.hypot(r2[:, 0], r2[:, 1])
    cosine = np.clip(np.sum(r1 * r2, axis=1) / np.maximum(n1 * n2, _TINY), -1.0, 1.0)
    angle = np.arccos(cosine)
    cross = r1[:, 0] * r2[:, 1] - r1[:, 1] * r2[:, 0]
    angle = np.where((cross < 0) != retrograde, 2 * math.pi - angle, angle)
    angle = np.where(np.abs(angle - math.pi) < _HALF_TURN_EPS, math.pi + _HALF_TURN_EPS, angle)
    nought = (angle < _HALF_TURN_EPS) | (2 * math.pi - angle < _HALF_TURN_EPS)
    angle = np.where(nought, _HALF_TURN_EPS, angle)
    cosine = np.cos(angle)
    a_term = np.sin(angle) * np.sqrt(n1 * n2 / (1 - cosine))
    target = math.sqrt(mu) * tof

    def f_of(z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        c, s = _stumpff(z)
        y = n1 + n2 + a_term * (z * s - 1) / np.sqrt(c)
        bad = y < 0
        held = np.where(bad, 0.0, y)
        f = (held / c) ** 1.5 * s + a_term * np.sqrt(held) - target
        return np.where(bad, np.nan, f), y

    count = len(n1)
    lo = np.full(count, -4 * math.pi**2)
    hi = np.full(count, (2 * math.pi - _EDGE) ** 2)
    for _ in range(_DOUBLINGS):
        at_lo, _ = f_of(lo)
        deeper = ~np.isnan(at_lo) & (at_lo >= 0)
        if not deeper.any():
            break
        lo = np.where(deeper, lo * 2, lo)
    at_lo, _ = f_of(lo)
    at_hi, _ = f_of(hi)
    #: NaN counts as below, as in `astro._roots`: where the arc does not exist
    #: the function tends to `-sqrt(mu) tof`.
    ok = (
        (np.isnan(at_lo) | (at_lo < 0))
        & ~np.isnan(at_hi)
        & (at_hi > 0)
        & (tof > 0)
        & (n1 > 0)
        & (n2 > 0)
    )
    for _ in range(_STEPS):
        mid = 0.5 * (lo + hi)
        at_mid, _ = f_of(mid)
        below = np.isnan(at_mid) | (at_mid < 0)
        lo = np.where(below, mid, lo)
        hi = np.where(below, hi, mid)
    at_z, y = f_of(0.5 * (lo + hi))
    ok &= ~np.isnan(at_z) & (y > 0)
    y = np.where(ok, y, 1.0)
    g = a_term * np.sqrt(y / mu)
    ok &= np.abs(g) > _TINY
    g = np.where(ok, g, 1.0)
    f_l = 1 - y / n1
    gdot = 1 - y / n2
    v1 = (r2 - f_l[:, None] * r1) / g[:, None]
    v2 = (gdot[:, None] * r2 - r1) / g[:, None]
    v1[~ok] = np.nan
    v2[~ok] = np.nan
    return v1, v2, ok


def closest(mu: float, r1: Rows, v1: Rows, r2: Rows, v2: Rows) -> np.ndarray:
    """How near the star each **flown** zero-turn arc comes (`astro.closest`):
    its perihelion if it sets out inward and arrives outward, else an end."""
    inward = np.sum(r1 * v1, axis=1) < 0
    outward = np.sum(r2 * v2, axis=1) > 0
    n1 = np.hypot(r1[:, 0], r1[:, 1])
    n2 = np.hypot(r2[:, 0], r2[:, 1])
    momentum = r1[:, 0] * v1[:, 1] - r1[:, 1] * v1[:, 0]
    speed2 = np.sum(v1 * v1, axis=1)
    radial = np.sum(r1 * v1, axis=1)
    ex = ((speed2 - mu / n1) * r1[:, 0] - radial * v1[:, 0]) / mu
    ey = ((speed2 - mu / n1) * r1[:, 1] - radial * v1[:, 1]) / mu
    perihelion = momentum * momentum / (mu * (1 + np.hypot(ex, ey)))
    return np.where(inward & outward, perihelion, np.minimum(n1, n2))
