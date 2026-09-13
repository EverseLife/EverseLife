# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The helm through a flyby (D-341): four stages and the corrections between.

A crossing's helm re-solves an arc round the star every minute (`guide`), and
round the star alone that is flying the plan -- the planets pull a little and
the next minute's arc takes the little back. Toward a pass it is fighting the
plan: the world lent the pull accelerates and bends the hull by the tens of
units a day the flyby is for, and an arc re-solved without that world asks
for all of it back (measured: a pass priced at 45 flown for 97). So under a
flyby the helm stops re-solving once the hull is clear of the world it left,
and coasts -- burning only what a correction asks for.

* **depart** -- the crossing's chase (`guide.chase`), aimed at the point a
  star-only arc from the departure reaches at the pass (`Shot.aim`), with the
  window and the outward rule of D-316; until the hull leaves the sphere of
  the world it is leaving.
* **cruise** -- coast to the pass. Whenever the time left to it has halved
  since the last correction, ask what burn puts the periapsis back where the
  plan has it (`shoot.pass_fix`) and burn that. Inside the world's sphere the
  floor is watched on every step: a periapsis sinking under it is lifted at
  full thrust, whatever the corrections think.
* **onward** -- from the periapsis: the first correction is the planned burn
  there, solved to end on the target at the promised hour
  (`shoot.arrival_fix`); after it, the same whenever the time left halves.
* **final** -- within the target's sphere, or at the hour: the crossing's own
  helm, capture and all.

The corrections are shootings through the whole sky and cost a fraction of a
second to seconds: the tick runs them off its loop. Nothing here reads a row.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from src.sky._base import Body, System, place
from src.sky.flyby import sphere
from src.sky.guide import BURN, COAST, STILL, Helm, capture_reach, chase, steer
from src.sky.shoot import arrival_fix, pass_fix, periapsis

DEPART = "depart"
CRUISE = "cruise"
ONWARD = "onward"
FINAL = "final"

#: What the helm asks between steps.
PASS_FIX = "pass"
ARRIVAL_FIX = "arrival"

#: No correction nearer the event than half an hour: the last one before the
#: pass has already closed it to a hundredth of a unit, and a burn of minutes
#: laid over the periapsis itself would be a burn at the wrong place.
_LAST_FIX = 1.0 / 48.0
#: A correction whenever the time left has fallen to this share of what it
#: was at the last one: halving, so a leg of days is corrected a handful of
#: times and ever more finely toward its end.
_HALVED = 0.5
#: An arrival solve that still misses by more than this is not burnt, units:
#: the planned periapsis burn stands in for it, and the next correction,
#: later and shorter, closes what is left.
_ARRIVAL_ACCEPT = 0.5


@dataclass(frozen=True, slots=True)
class Route:
    """The flyby an order carries, in sky days and units."""

    via: Body
    #: The world the passage left, if it left one: the departure lasts while
    #: the hull is in its sphere.
    home: Body | None
    #: The periapsis moment and its radius, signed with the sense of the pass.
    at: float
    rp: float
    #: The departure's aim, relative to the world at `at` (`Shot.aim`).
    aim: tuple[float, float]
    #: The planned change of speed at the periapsis, signed.
    burn: float
    #: The hour the arc arrives at the target; the floor under the pass, units.
    arrive: float
    floor: float


@dataclass(frozen=True, slots=True)
class Leg:
    """Where in the flyby the helm is: kept on the order between ticks."""

    stage: str = DEPART
    #: A correction's burn still to give, units a day.
    pending: tuple[float, float] = (0.0, 0.0)
    #: Days left to the event at the last correction, or none yet this stage.
    mark: float | None = None


def steer_pass(
    system: System,
    target: Body,
    route: Route,
    leg: Leg,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
    *,
    a_max: float,
    dt: float,
) -> tuple[Helm, Leg, str | None]:
    """The burn for one step under a flyby, the leg after it, and the
    correction the helm wants solved before the step, if any."""
    p, vp = place(route.via, t)
    rel = np.asarray(r) - p[0]
    v_rel = np.asarray(v) - vp[0]
    gap = float(np.hypot(*rel))
    zone = sphere(system, route.via)
    if leg.stage == DEPART:
        home = route.home
        clear = home is None or float(np.hypot(*(np.asarray(r) - place(home, t)[0][0]))) > sphere(
            system, home
        )
        goal = place(route.via, route.at)[0][0] + np.asarray(route.aim)
        helm = chase(
            system,
            target,
            (float(goal[0]), float(goal[1])),
            route.at - t,
            t,
            r,
            v,
            a_max=a_max,
            dt=dt,
            spare=route.via.key,
        )
        #: Out of the sphere of the world left, and the departure burn done --
        #: what is left of it fits in one step. A weak hull leaves the sphere
        #: long before its burn ends, and a cruise that began there coasted on
        #: half a departure.
        if not (clear and float(np.hypot(*helm.thrust)) < a_max):
            return helm, leg, None
        leg = Leg(stage=CRUISE)
    if leg.stage == CRUISE:
        passing = gap < zone and float(np.dot(rel, v_rel)) >= 0.0
        if passing or (t >= route.at and gap >= zone):
            leg = Leg(stage=ONWARD)
        else:
            left = route.at - t
            if _due(leg, left):
                return _coast(), leg, PASS_FIX
    #: The floor, on every step of both coasting stages while the hull is in
    #: the world's sphere and still falling toward it: a hull late for its
    #: pass is onward before it gets there, and the floor holds all the same.
    if leg.stage in (CRUISE, ONWARD) and gap < zone and _sinking(route, rel, v_rel):
        return _lift(rel, route.rp, a_max), leg, None
    if leg.stage == ONWARD:
        there, moving = place(target, t)
        gap_in = float(np.hypot(r[0] - there[0, 0], r[1] - there[0, 1]))
        speed_in = float(np.hypot(v[0] - moving[0, 0], v[1] - moving[0, 1]))
        #: The crossing's helm takes over where it would itself be capturing,
        #: or at the target's sphere, whichever comes first: coming in fast on
        #: weak engines, the braking starts well outside the sphere.
        near = gap_in <= max(sphere(system, target), capture_reach(system, target, speed_in, a_max))
        if near or route.arrive - t < _LAST_FIX:
            leg = Leg(stage=FINAL)
        elif _due(leg, route.arrive - t):
            return _coast(), leg, ARRIVAL_FIX
    if leg.stage == FINAL:
        return steer(system, target, t, r, v, arrive=route.arrive, a_max=a_max, dt=dt), leg, None
    return _give(leg, a_max, dt)


def correct(
    system: System,
    target: Body,
    route: Route,
    leg: Leg,
    want: str,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
) -> Leg:
    """Solve the correction the helm asked for: the leg with its burn to give."""
    if want == PASS_FIX:
        burn = pass_fix(system, t, r, v, route.via, route.rp, route.at)
        return replace(leg, pending=burn or (0.0, 0.0), mark=route.at - t)
    #: The first correction past the periapsis is the planned burn there, laid
    #: along the way the hull is going round the world and solved from it.
    seed = (0.0, 0.0)
    if leg.mark is None:
        _, vp = place(route.via, t)
        v_rel = np.asarray(v) - vp[0]
        size = max(float(np.hypot(*v_rel)), STILL)
        seed = (float(v_rel[0] / size * route.burn), float(v_rel[1] / size * route.burn))
    burn, miss = arrival_fix(system, t, r, v, target, route.arrive, seed=seed)
    return replace(leg, pending=burn if miss <= _ARRIVAL_ACCEPT else seed, mark=route.arrive - t)


def _due(leg: Leg, left: float) -> bool:
    """Whether a correction is due: nothing still to burn, not too near the
    event, and the time left halved since the last one."""
    if leg.pending != (0.0, 0.0) or left <= _LAST_FIX:
        return False
    return leg.mark is None or left <= leg.mark * _HALVED


def _sinking(route: Route, rel: np.ndarray, v_rel: np.ndarray) -> bool:
    """Whether the pass ahead goes under the floor: the osculating periapsis
    round the world, still to come and lower than the floor."""
    signed, until = periapsis(route.via.mu, rel[None, :], v_rel[None, :])
    return bool(until[0] > 0.0 and abs(float(signed[0])) < route.floor)


def _coast() -> Helm:
    return Helm(thrust=(0.0, 0.0), phase=COAST, captured=False)


def _give(leg: Leg, a_max: float, dt: float) -> tuple[Helm, Leg, None]:
    """Burn what the last correction asked for, as fast as the engines give it."""
    pending = np.asarray(leg.pending, dtype=float)
    size = float(np.hypot(*pending))
    if size < STILL:
        return _coast(), replace(leg, pending=(0.0, 0.0)), None
    accel = min(a_max, size / dt)
    thrust = pending / size * accel
    left = pending - thrust * dt
    rest = (0.0, 0.0) if float(np.hypot(*left)) < STILL else (float(left[0]), float(left[1]))
    return (
        Helm(thrust=(float(thrust[0]), float(thrust[1])), phase=BURN, captured=False),
        replace(leg, pending=rest),
        None,
    )


def _lift(rel: np.ndarray, rp: float, a_max: float) -> Helm:
    """Full thrust across the fall, the way the pass goes round: the periapsis
    radius grows with the square of the angular momentum, and a burn across
    the line to the world is what adds to it."""
    gap = max(float(np.hypot(*rel)), STILL)
    sense = 1.0 if rp >= 0.0 else -1.0
    across = np.array([-rel[1], rel[0]]) / gap * sense
    return Helm(
        thrust=(float(across[0] * a_max), float(across[1] * a_max)), phase=BURN, captured=False
    )
