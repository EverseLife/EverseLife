# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The arithmetic of conics: Kepler and Lambert.

A library, not a rule of the game -- the way `units` is a way of storing and
not a property of the world. Nothing here knows a planet, a hull or a vault
constant: it takes orbits as three numbers, places as pairs, a gravitational
parameter, and answers with velocities and points. The numbers in it are the
mathematics' own -- exponents, tolerances, the scan of a root finder -- and
that is why the magic-number test does not look here; the game's numbers
(what a hull may fly, what it pays) live in `engine.ship.course` and come
from the vault (D-065, D-271).

Units are whatever the caller's are, as long as they agree: a length, a time,
and `mu` in length cubed over time squared.

Sources: Bate, Mueller & White; Curtis, *Orbital Mechanics for Engineering
Students* -- algorithms 3.4 (Kepler in the universal anomaly) and 5.2
(Lambert in universal variables).
"""

from __future__ import annotations

import math
from typing import NamedTuple

type Vec = tuple[float, float]
#: An orbit as the seed writes it: radius, period, phase at the epoch.
type Orbit = tuple[float, float, float]

# --- vectors ----------------------------------------------------------------


def norm(v: Vec) -> float:
    return math.hypot(v[0], v[1])


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1])


def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1]


def cross(a: Vec, b: Vec) -> float:
    return a[0] * b[1] - a[1] * b[0]


# --- circular orbits ----------------------------------------------------------


def mu_of(orbit: Orbit) -> float:
    """The central body's gravitational parameter read off one circular orbit
    (Kepler III): `4 pi^2 r^3 / T^2`."""
    radius, period, _ = orbit
    return 4 * math.pi**2 * radius**3 / period**2


def place(orbit: Orbit, t: float) -> tuple[Vec, Vec]:
    """Where a body on the orbit stands and how it moves, `t` after the epoch."""
    radius, period, phase = orbit
    angle = phase + math.tau * t / period
    speed = math.tau * radius / period
    return (
        (radius * math.cos(angle), radius * math.sin(angle)),
        (-speed * math.sin(angle), speed * math.cos(angle)),
    )


def synodic(one: Orbit, other: Orbit) -> float:
    """How often two bodies meet: `Ta Tb / |Ta - Tb|`."""
    if one[1] == other[1]:
        return math.inf
    return one[1] * other[1] / abs(one[1] - other[1])


def lap(mu: float, radius: float) -> float:
    """The period of a circular orbit at this radius."""
    return math.tau * math.sqrt(radius**3 / mu)


# --- Stumpff functions and the universal variable ----------------------------

_SMALL = 1e-9


def stumpff_c(z: float) -> float:
    if z > _SMALL:
        return (1 - math.cos(math.sqrt(z))) / z
    if z < -_SMALL:
        return (math.cosh(math.sqrt(-z)) - 1) / (-z)
    return 1 / 2


def stumpff_s(z: float) -> float:
    if z > _SMALL:
        root = math.sqrt(z)
        return (root - math.sin(root)) / root**3
    if z < -_SMALL:
        root = math.sqrt(-z)
        return (math.sinh(root) - root) / root**3
    return 1 / 6


#: How far from a half-turn the transfer angle is nudged: at exactly pi the
#: plane of the arc is undefined and the formulas divide by zero. A hair's
#: breadth off it the arc is as good, and nobody flies on the singular hour.
_HALF_TURN_EPS = 1e-4
#: Root-finding tolerances on the universal variable `z`.
_Z_TOL = 1e-10
_Z_STEPS = 80
#: The scan of an interval of `z` for sign changes, before the bisection.
_Z_SCAN = 24
#: How far the hyperbolic floor may be lowered: 2^20 times the textbook bound.
_Z_DOUBLINGS = 20
_TINY = 1e-12
_EDGE = 1e-6
#: Bracketing of Kepler's equation in the universal anomaly.
_CHI_DOUBLINGS = 200
#: Full turns round the central body a transfer is ever asked about.
MAX_REVS = 3


def lambert(
    mu: float, r1: Vec, r2: Vec, tof: float, revs: int = 0, *, retrograde: bool = False
) -> list[tuple[Vec, Vec]]:
    """Every arc from `r1` to `r2` in `tof` with `revs` full turns.

    The universal-variable formulation (Curtis alg. 5.2). Zero turns give one
    arc; each further count of turns gives two or none -- the flight time has
    a floor for a given number of turns, and above it the arc can be flown
    with a higher or a lower orbit. Returns the departure and arrival
    velocities of each.

    Prograde by default -- the way the bodies go. `retrograde` asks for the
    arc against them: nonsense for a slow passage, but a fast hull bound for a
    body a little way **behind** its own would otherwise be sent the long way
    round.
    """
    n1, n2 = norm(r1), norm(r2)
    if n1 <= 0 or n2 <= 0 or tof <= 0:
        return []
    cosine = max(-1.0, min(1.0, dot(r1, r2) / (n1 * n2)))
    angle = math.acos(cosine)
    if (cross(r1, r2) < 0) != retrograde:
        angle = math.tau - angle
    if abs(angle - math.pi) < _HALF_TURN_EPS:
        angle = math.pi + _HALF_TURN_EPS
        cosine = math.cos(angle)
    #: And the other singular hour: both places on one ray from the star, the
    #: transfer angle nought (or a full turn). Nudged the same hair's breadth.
    if angle < _HALF_TURN_EPS or math.tau - angle < _HALF_TURN_EPS:
        angle = _HALF_TURN_EPS
        cosine = math.cos(angle)
    #: The constant of the pair, signed with the transfer angle.
    a_term = math.sin(angle) * math.sqrt(n1 * n2 / (1 - cosine))
    if abs(a_term) < _TINY:  # pragma: no cover -- guarded by the nudge above
        return []
    root_mu = math.sqrt(mu)

    def y(z: float) -> float:
        return n1 + n2 + a_term * (z * stumpff_s(z) - 1) / math.sqrt(stumpff_c(z))

    def f(z: float) -> float:
        yy = y(z)
        if yy < 0:
            return math.nan
        return (
            (yy / stumpff_c(z)) ** (3 / 2) * stumpff_s(z) + a_term * math.sqrt(yy) - root_mu * tof
        )

    if revs == 0:
        #: A fast arc is a steep hyperbola, and its `z` lies far below the
        #: textbook's bound: the floor is lowered by doubling until the
        #: function changes sign there. Monotone on this branch, so one root.
        lo, hi = -4 * math.pi**2, (math.tau - _EDGE) ** 2
        for _ in range(_Z_DOUBLINGS):
            at_lo = f(lo)
            if math.isnan(at_lo) or at_lo < 0:
                break
            lo *= 2
    else:
        lo, hi = (math.tau * revs + _EDGE) ** 2, (math.tau * (revs + 1) - _EDGE) ** 2
    found: list[tuple[Vec, Vec]] = []
    for z in _roots(f, lo, hi):
        yy = y(z)
        f_l = 1 - yy / n1
        g_l = a_term * math.sqrt(yy / mu)
        gdot = 1 - yy / n2
        if abs(g_l) < _TINY:  # pragma: no cover -- a degenerate arc
            continue
        v1 = ((r2[0] - f_l * r1[0]) / g_l, (r2[1] - f_l * r1[1]) / g_l)
        v2 = ((gdot * r2[0] - r1[0]) / g_l, (gdot * r2[1] - r1[1]) / g_l)
        found.append((v1, v2))
    return found


def _roots(f, lo: float, hi: float) -> list[float]:
    """The zeros of `f` on `[lo, hi]`: a scan for sign changes, then bisection.

    `f` is NaN where the arc does not exist (negative `y`), and at the edge of
    that stretch it tends to `-sqrt(mu) tof`: so NaN counts as **negative**,
    and a positive value next to it hides a root. The scan is coarse on
    purpose -- the function is smooth and has at most two zeros on any
    interval the callers hand over.
    """

    def below(value: float) -> bool:
        return math.isnan(value) or value < 0

    step = (hi - lo) / _Z_SCAN
    grid = [lo + step * i for i in range(_Z_SCAN + 1)]
    values = [f(z) for z in grid]
    zeros: list[float] = []
    for i in range(_Z_SCAN):
        a, b = values[i], values[i + 1]
        if a == 0:
            zeros.append(grid[i])
            continue
        if below(a) == below(b):
            continue
        za, zb = grid[i], grid[i + 1]
        low_side = below(a)
        for _ in range(_Z_STEPS):
            mid = (za + zb) / 2
            if below(f(mid)) == low_side:
                za = mid
            else:
                zb = mid
            if zb - za < _Z_TOL:
                break
        zero = (za + zb) / 2
        if not math.isnan(f(zero)):
            zeros.append(zero)
    return zeros


def perihelion(mu: float, r: Vec, v: Vec) -> float:
    """The closest the conic through `(r, v)` comes to the central body."""
    h = cross(r, v)
    n = norm(r)
    v2 = dot(v, v)
    rv = dot(r, v)
    ex = ((v2 - mu / n) * r[0] - rv * v[0]) / mu
    ey = ((v2 - mu / n) * r[1] - rv * v[1]) / mu
    e = math.hypot(ex, ey)
    return h * h / (mu * (1 + e))


def closest(mu: float, r1: Vec, v1: Vec, r2: Vec, v2: Vec, revs: int) -> float:
    """The closest the **flown** arc comes to the central body.

    Not the conic's perihelion: a fast arc is a hyperbola whose perihelion lies
    behind the departure point, and the hull never goes there. The arc passes
    its perihelion only if it sets out inward and arrives outward, or makes a
    full turn; otherwise the nearest point is one of the two ends.
    """
    inward = dot(r1, v1) < 0
    outward = dot(r2, v2) > 0
    if revs > 0 or (inward and outward):
        return perihelion(mu, r1, v1)
    return min(norm(r1), norm(r2))


def propagate(mu: float, r0: Vec, v0: Vec, dt: float) -> tuple[Vec, Vec]:
    """Where a coasting body is `dt` after `(r0, v0)` (Curtis alg. 3.4).

    Kepler's equation in the universal anomaly. Its left side grows with the
    anomaly on every conic, so the root is bracketed by doubling and then
    bisected: slower than Newton, and it never runs off on a steep hyperbola
    the way Newton's first step does.
    """
    if dt <= 0:
        return r0, v0
    n0 = norm(r0)
    vr0 = dot(r0, v0) / n0
    alpha = 2 / n0 - dot(v0, v0) / mu
    root_mu = math.sqrt(mu)

    def kepler(chi: float) -> float:
        z = alpha * chi * chi
        c, s = stumpff_c(z), stumpff_s(z)
        return (
            n0 * vr0 / root_mu * chi * chi * c
            + (1 - alpha * n0) * chi**3 * s
            + n0 * chi
            - root_mu * dt
        )

    lo, hi = 0.0, root_mu * dt / n0 + _SMALL
    for _ in range(_CHI_DOUBLINGS):
        if kepler(hi) > 0:
            break
        lo, hi = hi, hi * 2
    for _ in range(_Z_STEPS):
        mid = (lo + hi) / 2
        if kepler(mid) < 0:
            lo = mid
        else:
            hi = mid
    chi = (lo + hi) / 2
    z = alpha * chi * chi
    c, s = stumpff_c(z), stumpff_s(z)
    f_l = 1 - chi * chi / n0 * c
    g_l = dt - chi**3 / root_mu * s
    r = (f_l * r0[0] + g_l * v0[0], f_l * r0[1] + g_l * v0[1])
    n = norm(r)
    fdot = root_mu / (n * n0) * (z * s - 1) * chi
    gdot = 1 - chi * chi / n * c
    v = (fdot * r0[0] + gdot * v0[0], fdot * r0[1] + gdot * v0[1])
    return r, v


def trace(mu: float, r0: Vec, v0: Vec, tof: float, points: int) -> tuple[Vec, ...]:
    """The arc as a polyline at equal time steps, both ends included."""
    return tuple(
        propagate(mu, r0, v0, tof * i / (points - 1))[0] if i else r0 for i in range(points)
    )


# --- the legs of a passage ------------------------------------------------------


class Leg(NamedTuple):
    v1: Vec
    v2: Vec
    dv_out: float
    dv_in: float
    perihelion: float
    revs: int


def legs(
    mu: float, here: tuple[Vec, Vec], there: tuple[Vec, Vec], tof: float, *, max_revs: int
) -> list[Leg]:
    """Every arc between two moving places, priced at both ends.

    Here rather than in the engine's `course` (D-289, wave 2): the numerics
    package `src.sky` plans with these too, and it sits below the rules.
    """
    (r1, vp1), (r2, vp2) = here, there
    legs: list[Leg] = []
    #: Both ways round for a direct arc; against the planets only a fast hull
    #: gains, and a fast hull does not make full turns.
    ways = [(revs, False) for revs in range(max_revs + 1)] + [(0, True)]
    for revs, retrograde in ways:
        for v1, v2 in lambert(mu, r1, r2, tof, revs, retrograde=retrograde):
            legs.append(
                Leg(
                    v1,
                    v2,
                    norm(sub(v1, vp1)),
                    norm(sub(v2, vp2)),
                    closest(mu, r1, v1, r2, v2, revs),
                    revs,
                )
            )
    return legs


def max_revs(mu: float, here: Orbit, there: Orbit, tof: float) -> int:
    """How many full turns a flight this long could possibly make.

    A bound, not an answer: an arc cannot turn faster than a circle at the
    inner of the two radii, so more turns than that fit in `tof` is not worth
    asking the solver about.
    """
    inner = min(here[0], there[0])
    return min(MAX_REVS, int(tof / lap(mu, inner)))
