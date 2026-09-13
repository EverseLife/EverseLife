# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The flyby, searched (D-341): a passage bent round a third world.

Two legs and a pass. The first leg is a Lambert arc round the star from the
departure to the world lent the pull; the second, from that world to the
target. Between them the world turns the hull's excess velocity -- for
nothing up to the angle a hyperbola at the periapsis gives -- and the engines
pay only what the turn cannot: the change of the excess's size, burnt at the
periapsis where the speed is highest (a powered flyby, Oberth's gain). Both
ends are priced as a direct arc's are (`plan.escape_dv`), so the two compare
honestly hour for hour.

This is the patched-conic search, and it is only the first half. Near
Pyroxis -- three hundred Earths since D-324 -- the conic gets the price right
and the aim wrong: the pass it names is units off and hours early or late, and
a sixth of the passes it names do not exist under five bodies at all. So every
candidate this module finds is refined in the whole sky (`sky.shoot`) before
it is offered, and what is offered is what the refinement found.

The search is one batch: every moment of the pass on a geometric grid against
every flight time on the slider's, both ways round on each leg, for every
world that is neither end.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from src.sky._base import Body, System, park_of, place
from src.sky.lambert import arcs, closest
from src.units import HOURS_PER_DAY

#: The pass moments are searched on a geometric grid this fine: a step of 2%
#: of the moment. The refinement moves the moment freely afterwards, so the
#: grid need only land near the valley, not in its floor (measured: the best
#: price found moves by a tenth of a percent between 2% and 1%).
_PASS_STEP = 1.02
#: The bisection of the periapsis radius, in the log of it: the free turn
#: falls monotonically with the radius, and forty-eight halvings of the span
#: between the floor and the sphere of influence are far past a hair.
_RADIUS_STEPS = 48


@dataclass(frozen=True, slots=True)
class Candidate:
    """One flyby the conics found: the best through one world at one hour."""

    hours: float
    via: str
    #: The pass, days after the departure; the periapsis radius, units.
    tau: float
    rp: float
    #: What leaving, the pass and arriving cost, units a day, and their sum.
    dv_out: float
    dv_pass: float
    dv_in: float
    dv: float
    #: The excess velocity at the world, in and out, relative to it.
    v_in: tuple[float, float]
    v_out: tuple[float, float]


def sphere(system: System, body: Body) -> float:
    """The world's sphere of influence (Laplace): `a (mu / mu_star)^(2/5)`.

    Inside it the world's pull outweighs the star's tide on the hull's motion
    relative to it -- where a pass is a pass, and beyond which the free turn a
    hyperbola gives is no longer the world's to give. Derived, not tuned.
    """
    return body.orbit[0] * (body.mu / system.mu) ** 0.4


def turn(rp: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The free turn of a pass at periapsis `rp`, with `a` and `b` the in and
    out excess squared over the world's `mu`: `asin(1/e_in) + asin(1/e_out)`."""
    return np.arcsin(1.0 / (1.0 + rp * a)) + np.arcsin(1.0 / (1.0 + rp * b))


def pass_cost(
    v_in: np.ndarray, v_out: np.ndarray, mu: float, floor: float, zone: float
) -> tuple[np.ndarray, np.ndarray]:
    """What a pass costs and where its periapsis is, for rows of excesses.

    The turn between the two excesses must be one the world gives for nothing
    at a periapsis between the floor and its sphere: sharper than the floor
    allows would take a burn to rotate the excess, and a pass is not a
    rotation paid for; gentler than the sphere gives is a burn in the deep
    with a world nearby, not a flyby. Both are infinite here. Within the band
    the periapsis is where the turn is exact, and the cost is the change of
    speed there: `|sqrt(v_out^2 + 2 mu / rp) - sqrt(v_in^2 + 2 mu / rp)|`.
    """
    speed_in = np.hypot(v_in[:, 0], v_in[:, 1])
    speed_out = np.hypot(v_out[:, 0], v_out[:, 1])
    cosine = np.sum(v_in * v_out, axis=1) / np.maximum(speed_in * speed_out, 1e-12)
    wanted = np.arccos(np.clip(cosine, -1.0, 1.0))
    a = speed_in * speed_in / mu
    b = speed_out * speed_out / mu
    lo = np.full(len(speed_in), math.log(floor))
    hi = np.full(len(speed_in), math.log(zone))
    for _ in range(_RADIUS_STEPS):
        mid = 0.5 * (lo + hi)
        #: Still turning more than wanted: the periapsis goes higher.
        more = turn(np.exp(mid), a, b) > wanted
        lo = np.where(more, mid, lo)
        hi = np.where(more, hi, mid)
    rp = np.exp(0.5 * (lo + hi))
    inside = (wanted <= turn(np.full_like(a, floor), a, b)) & (
        wanted >= turn(np.full_like(a, zone), a, b)
    )
    pull = 2.0 * mu / rp
    cost = np.abs(np.sqrt(speed_out * speed_out + pull) - np.sqrt(speed_in * speed_in + pull))
    return np.where(inside, cost, np.inf), rp


def escape(body: Body, park: float, v_inf: np.ndarray) -> np.ndarray:
    """`plan.escape_dv` over rows: leaving the parking circle with this excess."""
    return np.sqrt(v_inf * v_inf + 2 * body.mu / park) - math.sqrt(body.mu / park)


def search(
    system: System,
    r0: tuple[float, float],
    v0: tuple[float, float],
    t0: float,
    target: Body,
    hours: tuple[float, ...],
    *,
    leaving: Body | None,
    floor_radii: float,
    shortest: float,
) -> dict[float, list[Candidate]]:
    """Every hour's best flyby through each world, cheapest first.

    `r0` is where the passage starts: the hull's own place, on the parking
    circle of `leaving` or adrift, as the direct preview starts from it.
    Leaving a world is priced by its escape from the circle, by the excess
    over the world's own velocity; leaving a drift, by the whole difference
    of velocity from `v0`. `shortest` is the least leg, days -- the slider's
    own first hour, whatever hours this search is asked for.
    """
    days = np.asarray(hours, dtype=float) / HOURS_PER_DAY
    if not len(days) or days.max() <= shortest:
        return {}
    moments = shortest * _PASS_STEP ** np.arange(
        int(math.ceil(math.log(days.max() / shortest) / math.log(_PASS_STEP)))
    )
    here = np.asarray(r0, dtype=float)
    base = place(leaving, t0)[1][0] if leaving is not None else np.asarray(v0, dtype=float)
    goal_r, goal_v = place(target, t0 + days)
    park_in = park_of(system, target)
    #: Which pass moment goes with which flight time: every pass strictly
    #: before the arrival.
    pair_pass, pair_hour = np.nonzero(moments[:, None] < days[None, :])
    found: dict[float, list[Candidate]] = {}
    for via in system.bodies:
        if via.key == target.key or (leaving is not None and via.key == leaving.key):
            continue
        if len(pair_pass) == 0:
            break
        via_r, via_v = place(via, t0 + moments)
        count = len(moments)
        first: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = []
        for retrograde in (False, True):
            v11, v12, ok = arcs(
                system.mu,
                np.repeat(here[None], count, 0),
                via_r,
                moments,
                np.full(count, retrograde),
            )
            ok &= closest(system.mu, np.repeat(here[None], count, 0), v11, via_r, v12) >= (
                system.corona
            )
            excess = np.hypot(*(v11 - base).T)
            out = escape(leaving, park_of(system, leaving), excess) if leaving else excess
            first.append((v11, v12, ok, out))
        for retrograde in (False, True):
            v21, v22, ok2 = arcs(
                system.mu,
                via_r[pair_pass],
                goal_r[pair_hour],
                days[pair_hour] - moments[pair_pass],
                np.full(len(pair_pass), retrograde),
            )
            ok2 &= (
                closest(system.mu, via_r[pair_pass], v21, goal_r[pair_hour], v22) >= system.corona
            )
            arrive = escape(target, park_in, np.hypot(*(v22 - goal_v[pair_hour]).T))
            v_out = v21 - via_v[pair_pass]
            for _v11, v12, ok1, out in first:
                ok = ok1[pair_pass] & ok2
                v_in = v12[pair_pass] - via_v[pair_pass]
                cost, rp = pass_cost(
                    np.where(ok[:, None], v_in, 1.0),
                    np.where(ok[:, None], v_out, 1.0),
                    via.mu,
                    floor_radii * via.radius,
                    sphere(system, via),
                )
                total = np.where(ok, out[pair_pass] + cost + arrive, np.inf)
                _keep(
                    found,
                    hours,
                    via.key,
                    total,
                    pair_pass,
                    pair_hour,
                    moments,
                    rp,
                    out,
                    cost,
                    arrive,
                    v_in,
                    v_out,
                )
    for hour in found:
        found[hour].sort(key=lambda one: one.dv)
    return found


def _keep(
    found: dict[float, list[Candidate]],
    hours: tuple[float, ...],
    via: str,
    total: np.ndarray,
    pair_pass: np.ndarray,
    pair_hour: np.ndarray,
    moments: np.ndarray,
    rp: np.ndarray,
    out: np.ndarray,
    cost: np.ndarray,
    arrive: np.ndarray,
    v_in: np.ndarray,
    v_out: np.ndarray,
) -> None:
    """The cheapest row of every hour, kept if it beats what this world already
    has there from the other way round."""
    order = np.lexsort((total, pair_hour))
    first_of = np.flatnonzero(np.r_[True, np.diff(pair_hour[order]) != 0])
    for row in order[first_of]:
        if not np.isfinite(total[row]):
            continue
        hour = hours[int(pair_hour[row])]
        kept = found.setdefault(hour, [])
        same = next((i for i, one in enumerate(kept) if one.via == via), None)
        if same is not None and kept[same].dv <= total[row]:
            continue
        one = Candidate(
            hours=hour,
            via=via,
            tau=float(moments[pair_pass[row]]),
            rp=float(rp[row]),
            dv_out=float(out[pair_pass[row]]),
            dv_pass=float(cost[row]),
            dv_in=float(arrive[row]),
            dv=float(total[row]),
            v_in=(float(v_in[row, 0]), float(v_in[row, 1])),
            v_out=(float(v_out[row, 0]), float(v_out[row, 1])),
        )
        if same is None:
            kept.append(one)
        else:
            kept[same] = one
