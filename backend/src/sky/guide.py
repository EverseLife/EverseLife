# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The autopilot's hand on the throttle (D-289): what to burn this step.

Every tick the helm re-solves the passage from where the hull actually is:
a Lambert arc from here to where the planet will be at the planned hour,
and the difference between the velocity that arc wants and the one the hull
has is what the engines burn -- as much of it as the thrust allows in the
step. Far out that is a burn and a long coast; from the braking distance in
it is the capture: shed the speed along a profile of the way left, match the
circle, and when the hull is close enough and slow enough it is put on it.

Nothing here reads a row: states in, a burn out. What the burn costs in fuel
and whether the tanks can pay is the tick's business (`ship.sim`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src import astro
from src.sky._base import Body, System, circle_speed, place

#: The helm starts braking as soon as the way left is what braking at this
#: thrust needs, with this margin. How many parking radii it matches the
#: circle within whatever its speed is the vault's (`orbit.approach_radii`).
BRAKE_MARGIN = 1.5
#: The share of the thrust the braking profile is laid with: the rest is
#: kept for the planet's pull and for the sky disagreeing with the profile.
BRAKE_SHARE = 0.85
#: A difference of velocity below this is a coast, not a burn: units a day.
STILL = 1e-3

#: The three things the helm can be doing.
BURN = "burn"
COAST = "coast"
CAPTURE = "capture"


@dataclass(frozen=True, slots=True)
class Helm:
    """What the helm decided for one step."""

    #: The acceleration to hold over the step, units a day squared; nought is a coast.
    thrust: tuple[float, float]
    phase: str
    #: Whether the hull is on the circle after this step: moor it.
    captured: bool


def brake_days(dv: float, a_max: float) -> float:
    """How much later than the impulsive plan a hull of this thrust arrives:
    the braking is a stretch, not an instant, and the hull is slower over
    all of it -- half the stretch's length, near enough."""
    if a_max <= 0:
        return 0.0
    return dv / (2.0 * a_max * BRAKE_SHARE)


def steer(
    system: System,
    target: Body,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
    *,
    arrive: float,
    a_max: float,
    dt: float,
) -> Helm:
    """The burn for one step of `dt` days, given where the hull is and when
    it means to arrive. `a_max` is the hull's acceleration, units a day squared."""
    p, vp = place(target, t)
    rel = np.array(r) - p[0]
    v_rel = np.array(v) - vp[0]
    gap = float(np.hypot(*rel))
    speed = float(np.hypot(*v_rel))
    #: The way braking needs from this speed at this thrust: past that line
    #: the arc is no longer chased, the speed is shed.
    brake = speed * speed / (2.0 * a_max) if a_max > 0 else float("inf")
    if gap <= max(system.approach * system.park, system.park + BRAKE_MARGIN * brake):
        return _capture(system, target, rel, v_rel, a_max=a_max, dt=dt)
    tof = arrive - t
    if tof <= dt:
        #: The hour has come and the planet is not here: a new arc at about
        #: the speed the hull has -- not a sprint -- and the same question
        #: next step.
        tof = max(system.late_leg, gap / max(speed, circle_speed(target, system.park)))
    goal = place(target, t + tof)[0][0]
    wanted = _lambert_velocity(system.mu, r, (float(goal[0]), float(goal[1])), tof, v)
    if wanted is None:
        return Helm(thrust=(0.0, 0.0), phase=COAST, captured=False)
    need = np.array(wanted) - np.array(v)
    size = float(np.hypot(*need))
    if size < STILL:
        return Helm(thrust=(0.0, 0.0), phase=COAST, captured=False)
    accel = min(a_max, size / dt)
    thrust = need / size * accel
    return Helm(thrust=(float(thrust[0]), float(thrust[1])), phase=BURN, captured=False)


def _capture(
    system: System,
    target: Body,
    rel: np.ndarray,
    v_rel: np.ndarray,
    *,
    a_max: float,
    dt: float,
) -> Helm:
    """Shed the speed and match the circle.

    The wanted velocity, relative to the planet, is a profile of the way
    left: inward, at the speed one can still brake to nought by the circle
    with a share of the thrust -- and over the last radii blended into the
    circle's own velocity, prograde. Once the hull is inside the capture
    radius and within the capture speed of the circle, it is on it.
    """
    gap = float(np.hypot(*rel))
    park = system.park
    inward = -rel / max(gap, 1e-9)
    around = np.array([-rel[1], rel[0]]) / max(gap, 1e-9)
    on_circle = around * circle_speed(target, park)
    if gap <= system.capture_radius and float(np.hypot(*(on_circle - v_rel))) <= (
        system.capture_speed
    ):
        return Helm(thrust=(0.0, 0.0), phase=CAPTURE, captured=True)
    left = max(gap - park, 0.0)
    closing = inward * float(np.sqrt(2.0 * BRAKE_SHARE * a_max * left))
    share = float(np.clip(1.0 - left / ((system.approach - 1.0) * park), 0.0, 1.0))
    wanted = (1.0 - share) * closing + share * on_circle
    need = wanted - v_rel
    size = float(np.hypot(*need))
    if size < STILL:
        return Helm(thrust=(0.0, 0.0), phase=CAPTURE, captured=False)
    accel = min(a_max, size / dt)
    thrust = need / size * accel
    return Helm(thrust=(float(thrust[0]), float(thrust[1])), phase=CAPTURE, captured=False)


def _lambert_velocity(
    mu: float,
    r: tuple[float, float],
    goal: tuple[float, float],
    tof: float,
    v: tuple[float, float],
) -> tuple[float, float] | None:
    """The departure velocity of the cheapest arc from `r` to `goal` in `tof`:
    the one nearest the hull's own velocity, prograde first."""
    best: tuple[float, float] | None = None
    cost = np.inf
    for retrograde in (False, True):
        for v1, _ in astro.lambert(mu, r, goal, tof, 0, retrograde=retrograde):
            gap = float(np.hypot(v1[0] - v[0], v1[1] - v[1]))
            if gap < cost:
                best, cost = v1, gap
    return best
