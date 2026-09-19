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
from src.sky._base import Rows, System, norms, place
from src.sky.bound import Bound, bound_to
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
    #: The planet a lap goes round (D-354): its `trace` is then drawn round
    #: that planet's centre, not in the star's frame -- the planet moves on,
    #: and a lap pinned where it stood at the start would be left behind.
    around: str | None = None


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

    The watcher only notes the verdict, and the span goes in slices so that a
    slice that comes back with one is the last flown -- the same shape
    `inertia` uses. (`advance` lets a watcher halt its rows since D-341; one
    row and a slice of hours need no such thing.)
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
    #: dips within half a radius of the ground nor reaches past a fifth of
    #: the planet's Hill radius (`sky.bound`, measured to keep for ninety
    #: days) -- is stable by
    #: arithmetic, and ninety days of five-body steps at the pace the
    #: planet's pull demands were the dearest thing the tick did. One lap of
    #: the ellipse is the line to draw. A wider ellipse is flown: the star's
    #: tide can pump it into the ground within days.
    held = bound_to(system, t0, r0, v0)
    if held is not None:
        return Fate(
            kind=STABLE,
            at=end,
            body=None,
            trace=_lap(held, points),
            span=held.period,
            loops=True,
            around=held.body.key,
        )
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

    #: Flown in slices so that a found end stops the flight: a slice that
    #: comes back with a verdict is the last one flown.
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


def _lap(held: Bound, points: int) -> tuple[tuple[float, float], ...]:
    """One lap of the bound orbit, round its planet's centre: the chart and
    the target line put the planet under it where the planet is (D-354)."""
    lap = astro.trace(held.body.mu, held.rel, held.v_rel, held.period, points)
    return tuple((float(x), float(y)) for x, y in lap)


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
