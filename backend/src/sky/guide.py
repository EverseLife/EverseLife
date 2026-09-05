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

import math
from dataclasses import dataclass

import numpy as np

from src import astro
from src.sky._base import (
    Body,
    Drifter,
    Star,
    System,
    Target,
    circle_rate,
    circle_speed,
    place_any,
    star_circle,
)

#: The helm starts braking as soon as the way left is what braking at this
#: thrust needs, with this margin. How many parking radii it matches the
#: circle within whatever its speed is the vault's (`orbit.approach_radii`).
BRAKE_MARGIN = 1.5
#: The share of the thrust the braking profile is laid with: the rest is
#: kept for the planet's pull and for the sky disagreeing with the profile.
BRAKE_SHARE = 0.85
#: A difference of velocity below this is a coast, not a burn: units a day.
STILL = 1e-3
#: How far above the ground a fall must still pass for the helm to let it
#: run: nearer than this many planet radii it is a crash, not an approach.
CLEARS = 1.6

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
    the fall to the circle, and the braking on it.

    Two stretches, not one. The braking is not an instant and the hull is
    slower over all of it -- half its length, near enough. Before that comes
    the fall (D-316): the helm takes the arc up at `BRAKE_MARGIN` braking
    distances out and lets the pull bring the hull down, which at the
    approach speed takes `BRAKE_MARGIN / 2` of the same `dv / a_max` again.
    The old word counted only the braking and the console promised an hour
    the fall then missed.
    """
    if a_max <= 0:
        return 0.0
    return dv / a_max * (BRAKE_MARGIN / 2.0 + 1.0 / (2.0 * BRAKE_SHARE))


def steer(
    system: System,
    target: Target,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
    *,
    arrive: float,
    a_max: float,
    dt: float,
) -> Helm:
    """The burn for one step of `dt` days, given where the hull is and when
    it means to arrive. `a_max` is the hull's acceleration, units a day squared.

    A crossing to a planet leaves the world it is on before it chases the arc
    (D-316): the burn is kept from carrying the hull inward while it is still
    in some other planet's hold. The arc is solved round the star alone, and
    from the far side of a parking circle the velocity it asks for points
    across the planet the hull is leaving -- which the hull then flew through,
    the ground being unchecked under an order (OQ-120) and a point mass
    flinging out what passes near its centre.
    """
    if isinstance(target, Star):
        return _circle(system, r, v, a_max=a_max, dt=dt)
    p, vp = place_any(target, t)
    rel = np.array(r) - p[0]
    v_rel = np.array(v) - vp[0]
    gap = float(np.hypot(*rel))
    speed = float(np.hypot(*v_rel))
    #: The way braking needs from this speed at this thrust: past that line
    #: the arc is no longer chased, the speed is shed.
    brake = speed * speed / (2.0 * a_max) if a_max > 0 else float("inf")
    if isinstance(target, Drifter):
        #: A hull, not a planet (D-289, wave 3): nothing to circle, only a
        #: point to come to rest beside -- and the approach profile is the
        #: whole of the helm from the first minute. An arc round the star
        #: re-solved every step to a point a few units off, moving with a
        #: hull rather than a planet, was a helm burning back and forth and,
        #: once in a while, running away at a thousand units a day; the
        #: profile asks for speed toward the target and never for more than
        #: the way left can shed. The order's hour stays the console's word.
        return _meet(system, rel, v_rel, a_max=a_max, dt=dt)
    if gap <= max(system.approach * system.park, system.park + BRAKE_MARGIN * brake):
        return _capture(system, target, rel, v_rel, a_max=a_max, dt=dt)
    tof = arrive - t
    if tof <= dt:
        #: The hour has come and the target is not here: a new arc at about
        #: the speed the hull has -- not a sprint -- and the same question
        #: next step.
        own = (
            circle_speed(target, system.park)
            if isinstance(target, Body)
            else float(np.hypot(*vp[0]))
        )
        tof = max(system.late_leg, gap / max(speed, own, STILL))
    goal = place_any(target, t + tof)[0][0]
    wanted = _lambert_velocity(system.mu, r, (float(goal[0]), float(goal[1])), tof, v)
    if wanted is None:
        return Helm(thrust=(0.0, 0.0), phase=COAST, captured=False)
    if eject_wait(system, target, t, r, v, wanted) > 0.0:
        #: Turned the wrong way: the circle brings the hull round for nothing,
        #: while leaving from here would cost the walk round it under thrust
        #: (D-316). The order already counted this wait into the hour it
        #: promised (`sim.depart`), so the arc after it is the arc that was
        #: priced -- waiting against an hour fixed for an immediate departure
        #: is what makes an arc steeper than the engines can fly.
        return Helm(thrust=(0.0, 0.0), phase=COAST, captured=False)
    need = np.array(wanted) - np.array(v)
    size = float(np.hypot(*need))
    if size < STILL:
        return Helm(thrust=(0.0, 0.0), phase=COAST, captured=False)
    accel = min(a_max, size / dt)
    thrust = need / size * accel
    return Helm(thrust=_outward(system, target, t, r, v, thrust, dt), phase=BURN, captured=False)


def eject_wait(
    system: System,
    target: Target,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
    wanted: tuple[float, float],
) -> float:
    """The wait for the ejection window from wherever the hull is, days.

    One reading for the helm and for the order alike: which world holds the
    hull is asked here and nowhere else, so the hour an order promises and the
    hour the helm flies to cannot part company. Nought where no world holds it.
    """
    leaving = _holding(system, target, t, r)
    return 0.0 if leaving is None else _wait_days(system, leaving, t, r, v, wanted)


def _wait_days(
    system: System,
    leaving: Body,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
    wanted: tuple[float, float],
) -> float:
    """How long the circle still has to turn before the hull faces the way it
    means to leave, days; nought if the window is already open (D-316).

    With an excess ten times the planet's escape speed the departure hyperbola
    is all but straight, so the way out is the way the hull is already going
    round the planet -- and the window is simply the side of the circle that
    faces the arc. Waiting for it is free: the circle is a coast. Burning off
    the wrong side is not, and it cost thirty units of the fifty a departure
    took.
    """
    p, vp = place_any(leaving, t)
    v_rel = np.array(v) - vp[0]
    excess = np.array(wanted) - vp[0]
    going = float(np.hypot(*v_rel))
    out = float(np.hypot(*excess))
    if going < STILL or out < STILL:
        return 0.0
    v_rel = v_rel / going
    excess = excess / out
    turn = math.atan2(
        float(v_rel[0] * excess[1] - v_rel[1] * excess[0]), float(np.dot(v_rel, excess))
    )
    if abs(turn) <= system.eject_window:
        return 0.0
    rel = np.array(r) - p[0]
    #: Which way round the planet the hull goes decides which way the heading
    #: turns, and therefore how much of the circle is still to come.
    onward = float(rel[0] * v_rel[1] - rel[1] * v_rel[0]) >= 0.0
    ahead = turn if onward else -turn
    if ahead < 0.0:
        ahead += 2.0 * math.pi
    rate = abs(circle_rate(leaving, system.park))
    return ahead / rate if rate > 0.0 else 0.0


def _holding(system: System, target: Target, t: float, r: tuple[float, float]) -> Body | None:
    """The planet whose hold the hull is still in and which is not where it is
    going -- the world it is leaving, or one it is crossing over (D-316).

    Nothing for a hull in the deep, and nothing at the far end: coming down on
    the target's circle is the whole point of the arrival, and the same rule
    there would forbid it.
    """
    if not isinstance(target, Body):
        return None
    hold = system.approach * system.park
    found: Body | None = None
    nearest = hold
    for body in system.bodies:
        if body.key == target.key:
            continue
        p, _ = place_any(body, t)
        gap = float(np.hypot(r[0] - p[0, 0], r[1] - p[0, 1]))
        if gap < nearest:
            found, nearest = body, gap
    return found


def _outward(
    system: System,
    target: Target,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
    thrust: np.ndarray,
    dt: float,
) -> tuple[float, float]:
    """The burn with what would carry the hull inward taken off it (D-316).

    While the hull is in a planet's hold, the step may not leave it moving
    toward that planet: the inward part of the burn is dropped and what cancels
    the inward drift the hull already has is added, within the same thrust. The
    hull therefore climbs away from the world it is on -- gaining speed round
    it, as a departure does -- and takes the arc up once it is clear.
    """
    body = _holding(system, target, t, r)
    if body is None:
        return (float(thrust[0]), float(thrust[1]))
    p, vp = place_any(body, t)
    rel = np.array(r) - p[0]
    gap = float(np.hypot(*rel))
    out = rel / max(gap, 1e-9)
    v_rel = np.array(v) - vp[0]
    if float(np.dot(v_rel + thrust * dt, out)) >= 0.0:
        return (float(thrust[0]), float(thrust[1]))
    inward = float(np.dot(thrust, out))
    drift = max(0.0, -float(np.dot(v_rel, out)) / dt)
    fixed = thrust - inward * out + drift * out
    size = float(np.hypot(*fixed))
    top = float(np.hypot(*thrust))
    if size > top and size > 0.0:
        fixed = fixed / size * top
    return (float(fixed[0]), float(fixed[1]))


def _capture(
    system: System,
    target: Body,
    rel: np.ndarray,
    v_rel: np.ndarray,
    *,
    a_max: float,
    dt: float,
) -> Helm:
    """Fall to the circle, then match it (D-316).

    The planet's pull brings a hull down for nothing, and braking high in
    its well pays for what the well gives. The helm used to do exactly that
    -- hold an inward profile from three to six radii out, shedding speed
    the fall then handed straight back -- and it cost five to nine times
    what the arrival is priced at. So while the hull is coming down, is
    still above the circle, and its fall clears the ground, nothing is
    burnt; at the circle the burn matches the circle. Once inside the
    capture radius and within the capture speed of it, the hull is on it.
    """
    gap = float(np.hypot(*rel))
    park = system.park
    around = np.array([-rel[1], rel[0]]) / max(gap, 1e-9)
    if (
        gap <= system.capture_radius
        and float(np.hypot(*(around * circle_speed(target, park) - v_rel))) <= system.capture_speed
    ):
        return Helm(thrust=(0.0, 0.0), phase=CAPTURE, captured=True)
    falling = float(np.dot(v_rel, rel)) / max(gap, 1e-9) < 0.0
    if falling and gap > park and _low_point(target.mu, rel, v_rel) > target.radius * CLEARS:
        return Helm(thrust=(0.0, 0.0), phase=CAPTURE, captured=False)
    #: At the circle, or on a fall that ends on the ground: match the circle
    #: through where the hull is, which is the circle itself once it is down.
    need = around * circle_speed(target, gap) - v_rel
    size = float(np.hypot(*need))
    if size < STILL:
        return Helm(thrust=(0.0, 0.0), phase=CAPTURE, captured=False)
    accel = min(a_max, size / dt)
    thrust = need / size * accel
    return Helm(thrust=(float(thrust[0]), float(thrust[1])), phase=CAPTURE, captured=False)


def _low_point(mu: float, rel: np.ndarray, v_rel: np.ndarray) -> float:
    """How near the planet this fall passes, if nothing is burnt: the
    periapsis of the two-body orbit the hull is on."""
    gap = float(np.hypot(*rel))
    speed = float(np.hypot(*v_rel))
    energy = speed * speed / 2.0 - mu / gap
    if abs(energy) < 1e-12:
        return gap
    momentum = float(rel[0] * v_rel[1] - rel[1] * v_rel[0])
    excess = 1.0 + 2.0 * energy * momentum * momentum / (mu * mu)
    return -mu / (2.0 * energy) * (1.0 - float(np.sqrt(max(0.0, excess))))


def _meet(
    system: System,
    rel: np.ndarray,
    v_rel: np.ndarray,
    *,
    a_max: float,
    dt: float,
) -> Helm:
    """Come to rest beside another hull: shed the relative speed along a
    profile of the way left, and once inside the hold's radius at under the
    hold's speed, the two fly as one (D-289, wave 3)."""
    gap = float(np.hypot(*rel))
    speed = float(np.hypot(*v_rel))
    if gap <= system.dock_radius and speed <= system.dock_speed:
        return Helm(thrust=(0.0, 0.0), phase=CAPTURE, captured=True)
    inward = -rel / max(gap, 1e-9)
    #: The profile aims at the middle of the hold's radius, so the hull
    #: arrives inside it with a little speed to spare rather than stopping
    #: on its edge.
    left = max(gap - system.dock_radius / 2.0, 0.0)
    wanted = inward * float(np.sqrt(2.0 * BRAKE_SHARE * a_max * left))
    need = wanted - v_rel
    size = float(np.hypot(*need))
    if size < STILL:
        return Helm(thrust=(0.0, 0.0), phase=CAPTURE, captured=False)
    accel = min(a_max, size / dt)
    thrust = need / size * accel
    return Helm(thrust=(float(thrust[0]), float(thrust[1])), phase=CAPTURE, captured=False)


def _circle(
    system: System,
    r: tuple[float, float],
    v: tuple[float, float],
    *,
    a_max: float,
    dt: float,
) -> Helm:
    """Match the circle round the star through the hull's own place, prograde
    -- the astrocentric orbit (D-289, 2026-09-04). Full thrust toward the
    circle's velocity, and once within the helm's stillness of it the order
    is done: the hull hangs on the circle like a planet, coasting."""
    need = star_circle(system, r) - np.array(v, dtype=float)
    size = float(np.hypot(*need))
    #: On it within the capture speed, as on a planet's circle: the pull of
    #: the planets shifts the wanted velocity a little every minute, and a
    #: helm chasing stillness would burn for ever.
    if size <= system.capture_speed:
        return Helm(thrust=(0.0, 0.0), phase=CAPTURE, captured=True)
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
