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
    capture_of,
    circle_rate,
    circle_speed,
    park_of,
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


def brake_days(system: System, dv: float, a_max: float, body: Body | None = None) -> float:
    """How much later than the impulsive plan a hull of this thrust arrives:
    the fall to the circle, and the braking on it.

    Two stretches, and the first of them has two shapes (OQ-136). The braking
    is not an instant and the hull is slower over all of it -- half its length,
    near enough. Before it comes the fall from wherever the helm took the arc
    up, and where that is depends on how fast the hull is coming: fast, and it
    is `BRAKE_MARGIN` braking distances out, so the fall grows with the speed;
    slow, and it is the flat `orbit.approach_radii` of the hold, so the fall
    *shrinks* with the speed -- the slower the approach, the longer the same
    few units take. One term cannot be both, which is why a single factor
    under-promised the fast end by five to seven hours and the cheap end by
    three and a half against four tenths.
    """
    if a_max <= 0:
        return 0.0
    speed = max(dv, STILL)
    #: The circle fallen to is the target world's own (D-324); a hull met in
    #: the deep has none, and then there is only the braking.
    park = park_of(system, body) if body is not None else 0.0
    fall = max(
        BRAKE_MARGIN / 2.0 * speed / a_max,
        (system.approach - 1.0) * park / speed,
    )
    return fall + speed / (2.0 * a_max * BRAKE_SHARE)


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
    if isinstance(target, Drifter):
        #: A hull, not a planet (D-289, wave 3): nothing to circle, only a
        #: point to come to rest beside -- and the approach profile is the
        #: whole of the helm from the first minute. An arc round the star
        #: re-solved every step to a point a few units off, moving with a
        #: hull rather than a planet, was a helm burning back and forth and,
        #: once in a while, running away at a thousand units a day; the
        #: profile asks for speed toward the target and never for more than
        #: the way left can shed. The order's hour stays the console's word.
        return _meet(system, target, t, r, v, rel, v_rel, a_max=a_max, dt=dt)
    park = park_of(system, target)
    if gap <= capture_reach(system, target, speed, a_max):
        return _capture(system, target, rel, v_rel, a_max=a_max, dt=dt)
    tof = arrive - t
    if tof <= dt:
        #: The hour has come and the target is not here: a new arc at about
        #: the speed the hull has -- not a sprint -- and the same question
        #: next step.
        own = circle_speed(target, park) if isinstance(target, Body) else float(np.hypot(*vp[0]))
        tof = max(system.late_leg, gap / max(speed, own, STILL))
    goal = place_any(target, t + tof)[0][0]
    return chase(system, target, (float(goal[0]), float(goal[1])), tof, t, r, v, a_max=a_max, dt=dt)


def capture_reach(system: System, target: Body, speed: float, a_max: float) -> float:
    """How far from a planet the arc stops being chased and the capture begins:
    the hold's flat radius, or -- coming in fast -- the way braking needs from
    this speed at this thrust, with the helm's margin. A flyby's helm hands
    over to the crossing's here too (D-341), so the two arrive alike."""
    park = park_of(system, target)
    brake = speed * speed / (2.0 * a_max) if a_max > 0 else float("inf")
    return max(system.approach * park, park + BRAKE_MARGIN * brake)


def chase(
    system: System,
    target: Target,
    goal: tuple[float, float],
    tof: float,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
    *,
    a_max: float,
    dt: float,
    spare: str | None = None,
) -> Helm:
    """Chase the arc to `goal` in `tof` days: burn toward the Lambert velocity
    from where the hull is, under the departure's rules (D-316).

    The arc of a crossing is aimed at the target's place at the planned hour;
    a flyby's first leg is aimed at a point beside the world lent the pull
    (D-341), and that world, `spare`, is no hold to be kept out of -- coming
    down toward it is the whole point of the leg.
    """
    wanted = _lambert_velocity(system.mu, r, goal, tof, v)
    if wanted is None:
        return Helm(thrust=(0.0, 0.0), phase=COAST, captured=False)
    #: The world that holds the hull, read once for the two questions that
    #: follow -- the tick asks them of every ordered hull every minute.
    leaving = _holding(system, target, t, r, spare=spare)
    if leaving is not None and _wait_days(system, leaving, t, r, v, wanted) > 0.0:
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
    return Helm(thrust=_outward(system, leaving, t, r, v, thrust, dt), phase=BURN, captured=False)


def eject_wait(
    system: System,
    target: Target,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
    wanted: tuple[float, float],
) -> float:
    """The wait for the ejection window from wherever the hull is, days.

    The whole question in one call, for the plan (`sky.preview`) and the order
    (`ship.sim.depart`); the helm asks the same two pieces apart, having read
    the holding world once for the burn as well. Which world that is is decided
    here alone, so the hour an order promises and the hour the helm flies to
    cannot part company. Nought where no world holds the hull.
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
    rate = abs(circle_rate(leaving, park_of(system, leaving)))
    return ahead / rate if rate > 0.0 else 0.0


def holding(
    system: System, target: Target, t: float, r: tuple[float, float], *, spare: str | None = None
) -> Body | None:
    """The world whose hold the hull is in and which is neither where it is
    going nor `spare` -- what a departure leaves (D-316, D-341)."""
    return _holding(system, target, t, r, spare=spare)


def _holding(
    system: System,
    target: Target,
    t: float,
    r: tuple[float, float],
    *,
    spare: str | None = None,
) -> Body | None:
    """The planet whose hold the hull is still in and which is not where it is
    going -- the world it is leaving, or one it is crossing over (D-316).

    Nothing for a hull in the deep, and nothing at the far end: coming down on
    the target's circle is the whole point of the arrival, and the same rule
    there would forbid it. Nor at `spare`, the world a flyby passes (D-341):
    its periapsis may lie inside its hold, and a hull kept out of it would be
    flung wide of the pass it was sent for.
    """

    #: Each world holds out to its own circle since D-324, and the circles
    #: are no longer one size: the hold is asked of the body, not of the
    #: system, so a hull four units from Terra is in its hold while the same
    #: four units from Pyroxis are still deep inside its.
    def hold_of(body: Body) -> float:
        return system.approach * park_of(system, body)

    #: The world the hull is going to, or -- for a hull as the target -- the
    #: world that hull is itself in the hold of: coming down toward either is
    #: the arrival, and the same rule there would forbid it.
    goal: str | None = None
    if isinstance(target, Body):
        goal = target.key
    elif isinstance(target, Drifter):
        theirs = place_any(target, t)[0][0]
        near: float | None = None
        for body in system.bodies:
            p, _ = place_any(body, t)
            gap = float(np.hypot(theirs[0] - p[0, 0], theirs[1] - p[0, 1]))
            if gap < hold_of(body) and (near is None or gap < near):
                goal, near = body.key, gap
    else:
        return None
    found: Body | None = None
    nearest: float | None = None
    for body in system.bodies:
        if body.key in (goal, spare):
            continue
        p, _ = place_any(body, t)
        gap = float(np.hypot(r[0] - p[0, 0], r[1] - p[0, 1]))
        if gap < hold_of(body) and (nearest is None or gap < nearest):
            found, nearest = body, gap
    return found


def _outward(
    system: System,
    body: Body | None,
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
    park = park_of(system, target)
    window = capture_of(system, target)
    around = np.array([-rel[1], rel[0]]) / max(gap, 1e-9)
    if (
        gap <= window
        and float(np.hypot(*(around * circle_speed(target, park) - v_rel))) <= system.capture_speed
    ):
        return Helm(thrust=(0.0, 0.0), phase=CAPTURE, captured=True)
    coming = -float(np.dot(v_rel, rel)) / max(gap, 1e-9)
    low = _low_point(target.mu, rel, v_rel)
    #: The fall is let run only while it would bring the hull **into the
    #: window the mooring watches**: above the circle, and no higher than the
    #: capture radius. Under the circle it goes through, and down there the
    #: burn finds a circle of its own -- a stable orbit at the wrong radius.
    #: Above the radius it never arrives: the hull matches the circle's speed
    #: where it is and turns there for ever. Both ends of that band have been
    #: measured as orders that never close.
    if coming > 0.0 and gap > park and park < low <= window:
        #: Coast while the speed is still one the way left can shed: `v² = 2ad`
        #: over what remains to the circle, at the profile's share of the
        #: thrust, plus the circle's own speed, which is not shed at all. The
        #: pull does the work for nothing up to that line and cannot be
        #: afforded past it -- a hull near `ship.min_thrust_ratio` that keeps
        #: falling sails through the circle and settles on whatever ring it
        #: reaches, which the mooring (measured against the circle's own speed)
        #: never recognises: an order that never closes. The line is exact for
        #: a fall and wants no margin.
        allowed = float(np.sqrt(2.0 * BRAKE_SHARE * a_max * (gap - park))) + circle_speed(
            target, park
        )
        if float(np.hypot(*v_rel)) <= allowed:
            return Helm(thrust=(0.0, 0.0), phase=CAPTURE, captured=False)
    if gap > window:
        #: Too high to be moored from, and not falling into the window on its
        #: own: come down, at the speed the way left can still shed. Matching
        #: the circle's speed up here would leave the hull turning at this
        #: radius for ever -- the right speed at the wrong place.
        wanted = (
            -rel / max(gap, 1e-9) * float(np.sqrt(2.0 * BRAKE_SHARE * a_max * max(gap - park, 0.0)))
        )
    else:
        #: In the window: match the circle the mooring is measured against --
        #: the one at `orbit.park_radii`, not the one through where the hull
        #: happens to be, which is a stable orbit at the wrong radius.
        wanted = around * circle_speed(target, park)
    need = wanted - v_rel
    size = float(np.hypot(*need))
    if size < STILL:
        return Helm(thrust=(0.0, 0.0), phase=CAPTURE, captured=False)
    accel = min(a_max, size / dt)
    thrust = need / size * accel
    return Helm(thrust=(float(thrust[0]), float(thrust[1])), phase=CAPTURE, captured=False)


def _low_point(mu: float, rel: np.ndarray, v_rel: np.ndarray) -> float:
    """How near the planet this fall passes, if nothing is burnt: the
    periapsis of the two-body orbit the hull is on."""
    gap = max(float(np.hypot(*rel)), 1e-9)
    speed = float(np.hypot(*v_rel))
    energy = speed * speed / 2.0 - mu / gap
    if abs(energy) < 1e-12:
        return gap
    momentum = float(rel[0] * v_rel[1] - rel[1] * v_rel[0])
    excess = 1.0 + 2.0 * energy * momentum * momentum / (mu * mu)
    return -mu / (2.0 * energy) * (1.0 - float(np.sqrt(max(0.0, excess))))


def _meet(
    system: System,
    target: Target,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
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
    #: And not through the world the chaser is still on (D-316): the profile
    #: aims at the other hull from the first minute, so a rescuer ordered off
    #: a parking circle would drive into its own planet exactly as a crossing
    #: used to.
    return Helm(
        thrust=_outward(system, _holding(system, target, t, r), t, r, v, thrust, dt),
        phase=CAPTURE,
        captured=False,
    )


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
