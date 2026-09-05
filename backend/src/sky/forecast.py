# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What inertia does to a hull that burns nothing (D-289): where it goes,
whether it comes down on a body or leaves the system, and when.

The reading the console shows beside every hull in space, and the reading a
loss is scheduled by: the hour the forecast names becomes a job, and the job
checks the same arithmetic again before it kills anybody.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src import astro
from src.sky._base import Body, Rows, System, norms, place
from src.sky.field import advance
from src.units import TRACE_POINTS

#: The three ends a coast can have.
STABLE = "stable"
CRASH = "crash"
ESCAPE = "escape"


@dataclass(frozen=True, slots=True)
class Fate:
    """Where the coast leads."""

    kind: str
    #: The moment it ends, sky days -- the horizon's end for a stable coast.
    at: float
    #: The body it comes down on: a planet's key, `star`, or nothing.
    body: str | None
    #: The path up to that moment, at equal time steps, map units.
    trace: tuple[tuple[float, float], ...]
    #: How long the line is, days from its start: the coast's whole length,
    #: or one lap of a bound ellipse -- which `loops`, and is read modulo it.
    span: float
    loops: bool


def ground_of(system: System, t: np.ndarray, r: Rows) -> tuple[str | None, bool]:
    """Whether the (one) row is on a body or out of the system right now.

    Asked of a coast as it is flown ahead (`inertia`) and of a hull under an
    order as the tick moves it (`ship.helm`): the ground is the same ground
    either way, and the two must not learn to disagree about it (OQ-120).
    """
    distance = float(norms(r)[0])
    if distance < system.corona:
        return "star", False
    if distance > system.edge:
        return None, True
    for body in system.bodies:
        p, _ = place(body, t)
        if float(norms(r - p)[0]) < body.radius:
            return body.key, False
    return None, False


def coast_to(
    system: System,
    t0: float,
    t1: float,
    r0: tuple[float, float],
    v0: tuple[float, float],
    *,
    dt_max: float,
) -> tuple[tuple[float, float], tuple[float, float], float, str | None, bool]:
    """Coast one hull from `t0` to `t1`, stopping where the ground takes it.

    Gives back where the hull ends up, when, and what took it there: the key
    of the body it struck, and whether it left the system. A stretch flown
    with no thrust is still a stretch through the same sky as a coast that
    is forecast, and the two must not learn to disagree about it (OQ-120) --
    so the ground is asked of every integrator step here as well, not only
    of the two ends. A tick catching up after an idle worker flies hours in
    one go, and a planet is small enough to pass clean through in one.

    `advance`'s watcher cannot break its own loop, so the span goes in
    slices and a slice that comes back with a verdict is the last one flown
    -- the same shape `inertia` uses.
    """
    t = np.array([t0], dtype=float)
    r = np.array([r0], dtype=float)
    v = np.array([v0], dtype=float)
    found: dict[str, object] = {}

    def watch(tt: np.ndarray, rr: Rows, vv: Rows) -> None:
        if found:
            return
        body, gone = ground_of(system, tt, rr)
        if body is not None or gone:
            found.update(
                at=float(tt[0]),
                body=body,
                gone=gone,
                r=(float(rr[0, 0]), float(rr[0, 1])),
                v=(float(vv[0, 0]), float(vv[0, 1])),
            )

    span = max(t1 - t0, 0.0)
    slices = max(1, int(np.ceil(span / max(dt_max, 1e-6) / 8)))
    for i in range(1, slices + 1):
        until = np.array([t0 + span * i / slices])
        r, v = advance(system, t, until, r, v, dt_max=dt_max, watch=watch)
        t = np.maximum(t, until)
        if found:
            break
    if found:
        return (
            found["r"],  # type: ignore[return-value]
            found["v"],  # type: ignore[return-value]
            float(found["at"]),  # type: ignore[arg-type]
            found["body"],  # type: ignore[return-value]
            bool(found["gone"]),
        )
    return (
        (float(r[0, 0]), float(r[0, 1])),
        (float(v[0, 0]), float(v[0, 1])),
        t1,
        None,
        False,
    )


def inertia(
    system: System,
    t0: float,
    r0: tuple[float, float],
    v0: tuple[float, float],
    *,
    horizon: float,
    dt_max: float,
    points: int = TRACE_POINTS,
) -> Fate:
    """Coast one hull for `horizon` days, or until it hits something or leaves.

    Watched step by step: the ground is a small target and a sample every
    day would fly a hull through Terra without noticing. The trace is drawn
    at equal steps up to the end, so the chart's clock hand walks it the
    way it walks a passage.
    """
    end = t0 + horizon
    #: Bound to a planet -- the parking circle, or any ellipse that neither
    #: grazes the ground nor reaches the edge of the planet's hold -- is
    #: stable by arithmetic, and ninety days of five-body steps at the pace
    #: the planet's pull demands were the dearest thing the tick did. One
    #: lap of the ellipse is the line to draw.
    bound = _bound_to(system, t0, r0, v0, points)
    if bound is not None:
        lap, period = bound
        return Fate(kind=STABLE, at=end, body=None, trace=lap, span=period, loops=True)
    t = np.array([t0], dtype=float)
    r = np.array([r0], dtype=float)
    v = np.array([v0], dtype=float)
    #: The path as flown, then resampled: the integrator steps as it likes,
    #: the chart wants equal times.
    times: list[float] = [t0]
    path: list[tuple[float, float]] = [(float(r0[0]), float(r0[1]))]
    found: dict[str, object] = {}

    def watch(tt: np.ndarray, rr: Rows, _vv: Rows) -> None:
        if found:
            return
        times.append(float(tt[0]))
        path.append((float(rr[0, 0]), float(rr[0, 1])))
        body, gone = ground_of(system, tt, rr)
        if body is not None:
            found.update(kind=CRASH, at=float(tt[0]), body=body)
        elif gone:
            found.update(kind=ESCAPE, at=float(tt[0]), body=None)

    #: Flown in slices so that a found end stops the flight: the watcher
    #: cannot break the loop, but a slice that comes back with a verdict is
    #: the last one flown.
    slices = max(1, int(np.ceil(horizon / max(dt_max, 1e-6) / 8)))
    for i in range(1, slices + 1):
        until = np.array([t0 + horizon * i / slices])
        r, v = advance(system, t, until, r, v, dt_max=dt_max, watch=watch)
        t = np.maximum(t, until)
        if found:
            break
    kind = str(found.get("kind", STABLE))
    at = float(found.get("at", end))
    body = found.get("body")
    return Fate(
        kind=kind,
        at=at,
        body=None if body is None else str(body),
        trace=_resample(times, path, at, points),
        span=at - t0,
        loops=False,
    )


#: How much of a planet's Hill radius a bound ellipse may reach before the
#: star's tug is no longer a perturbation: the classic third.
_HOLD_SHARE = 1 / 3


def _bound_to(
    system: System,
    t0: float,
    r0: tuple[float, float],
    v0: tuple[float, float],
    points: int,
) -> tuple[tuple[tuple[float, float], ...], float] | None:
    """One lap of the ellipse round the nearest planet, and its period, if
    the hull is on one that stays: negative two-body energy, periapsis above
    the ground, apoapsis well inside the planet's hold. Nothing otherwise."""
    if not system.bodies or system.mu <= 0:
        return None
    body, rel, v_rel = _nearest(system, t0, r0, v0)
    gap = astro.norm(rel)
    speed = astro.norm(v_rel)
    if gap <= 0:
        return None
    energy = speed * speed / 2 - body.mu / gap
    if energy >= 0:
        return None
    axis = -body.mu / (2 * energy)
    momentum = astro.cross(rel, v_rel)
    excess = 1 + 2 * energy * momentum * momentum / (body.mu * body.mu)
    eccentricity = float(np.sqrt(max(0.0, excess)))
    periapsis = axis * (1 - eccentricity)
    apoapsis = axis * (1 + eccentricity)
    hill = body.orbit[0] * (body.mu / (3 * system.mu)) ** (1 / 3)
    if periapsis <= body.radius or apoapsis >= hill * _HOLD_SHARE:
        return None
    centre = place(body, t0)[0][0]
    period = astro.lap(body.mu, axis)
    lap = astro.trace(body.mu, rel, v_rel, period, points)
    return tuple((float(centre[0]) + x, float(centre[1]) + y) for x, y in lap), period


def _nearest(
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


def _resample(
    times: list[float], path: list[tuple[float, float]], end: float, points: int
) -> tuple[tuple[float, float], ...]:
    """The flown path at `points` equal moments from its start to `end`."""
    if len(times) < 2:
        return tuple(path[:1]) * points
    stamps = np.array(times)
    xs = np.array([p[0] for p in path])
    ys = np.array([p[1] for p in path])
    wanted = np.linspace(times[0], end, points)
    return tuple(
        (float(x), float(y))
        for x, y in zip(np.interp(wanted, stamps, xs), np.interp(wanted, stamps, ys), strict=True)
    )
