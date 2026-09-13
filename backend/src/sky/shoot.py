# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The flyby in the whole sky (D-341): refined before it is offered, and
corrected while it is flown -- both by shooting through all five bodies.

**Refinement.** A conic pass (`sky.flyby`) is a guess about a periapsis:
where, when, how fast in and how fast out. From that periapsis the sky is
flown **both ways** -- back to the departure moment, on to the arrival -- and
the guess is moved until the backward line starts where the passage starts
and the forward line ends where the target will be. Flying out of the
periapsis and not into it is what makes this converge: a line aimed *at* a
heavy world from days away scatters with the digits of its aim, a line flown
*away* from the periapsis does not. What is left free is the periapsis radius
itself (five unknowns, four conditions), so the step is the least one
(least squares), and a radius that would go under the floor is pinned there.
Rows that fall into the corona or onto a world are halted and lost: a flyby
that exists only through the star is not offered.

**Corrections.** Under way the helm coasts, and now and then asks what burn
now puts the periapsis back where the plan has it (`pass_fix`), and after the
pass, what burn now ends the coast on the target at the promised hour
(`arrival_fix`). Each is a small Newton solve over a coast flown in the whole
sky; the helm burns the answer and coasts on. A solve that does not converge
is not burnt.

Nothing here reads a row.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

import numpy as np

from src import astro
from src.sky._base import Body, Rows, System, park_of, place
from src.sky.field import advance
from src.sky.flyby import Candidate, escape, sphere
from src.units import HOURS_PER_DAY

#: The finite-difference steps of the refinement's unknowns: the periapsis
#: angle (radians), its moment (days), the speeds in and out (units a day),
#: and the radius (units).
_NUDGE = np.array([1e-6, 1e-6, 1e-4, 1e-4, 1e-5])
#: The most one Newton step may move each unknown before it is scaled down:
#: a tenth of a radian, a fifth of a day, five units a day; the radius by half
#: of itself (below).
_REACH = np.array([0.1, 0.2, 5.0, 5.0])
_REACH_RADIUS = 0.5
#: Newton iterations, and halvings of a step that did not help.
_ITERATIONS = 8
_HALVINGS = 6
#: Converged: the backward and forward ends within this of their marks, units.
_MISS = 0.02
#: Offered: the final miss within this -- a refinement stopped just short of
#: the tolerance is still a flyby the helm's corrections close.
_OFFER_MISS = 0.05
#: The in-flight solves: the nudge of the burn (units a day), how far one
#: step may go, and the misses they are content with -- a periapsis within a
#: hundredth of a unit and a minute, an arrival within two hundredths.
_FIX_NUDGE = 1e-3
_FIX_REACH = 5.0
_ARRIVAL_REACH = 30.0
_FIX_RADIUS = 0.01
_FIX_MINUTE = 1.0 / 1440.0
_FIX_ITERATIONS = 8
_FIX_HALVINGS = 10
#: No cap on the integrator's step: every shooting here is flown at the
#: field's own rule, a twentieth of the nearest body's orbital time
#: (`field.STEP_SHARE`), which near a world is minutes and in the deep is a
#: day. Capped at the planner's hour (`orbit.plan_step_minutes`) the same
#: refinements came out equal to the hundredth of a unit and a hundredth of
#: a day, at three times the cost -- the cap only ever bit where the rule
#: already made the step accurate.
_OPEN = math.inf


@dataclass(frozen=True, slots=True)
class Shot:
    """A flyby that exists under five bodies: the plan an order carries."""

    candidate: Candidate
    #: The periapsis moment, days after the departure; its radius, signed with
    #: the sense of the pass (positive: counter-clockwise round the world).
    at: float
    rp: float
    #: The speeds at the periapsis before and after the burn there.
    speed_in: float
    speed_out: float
    #: The velocity the passage leaves with, heliocentric.
    v1: tuple[float, float]
    #: Where a star-only arc from the departure with `v1` stands at `at`,
    #: relative to the world then: what the helm aims the departure at.
    aim: tuple[float, float]
    #: What leaving, the pass and arriving cost, units a day, and the sum.
    dv_out: float
    dv_pass: float
    dv_in: float
    dv: float


def seed_of(via: Body, candidate: Candidate, t0: float) -> tuple[np.ndarray, float]:
    """The conic's periapsis as the refinement's first guess, and its sense.

    The periapsis lies at the outgoing asymptote's true anomaly back from the
    outgoing excess: `theta = angle(v_out) - sense * acos(-1 / e_out)`.
    """
    v_in = np.array(candidate.v_in)
    v_out = np.array(candidate.v_out)
    sense = math.copysign(1.0, v_in[0] * v_out[1] - v_in[1] * v_out[0])
    speed_in = float(np.hypot(*v_in))
    speed_out = float(np.hypot(*v_out))
    e_out = 1.0 + candidate.rp * speed_out * speed_out / via.mu
    theta = math.atan2(v_out[1], v_out[0]) - sense * math.acos(-1.0 / e_out)
    pull = 2.0 * via.mu / candidate.rp
    return (
        np.array(
            [
                theta,
                t0 + candidate.tau,
                math.sqrt(speed_in * speed_in + pull),
                math.sqrt(speed_out * speed_out + pull),
                candidate.rp,
            ]
        ),
        sense,
    )


def refine(
    system: System,
    r0: tuple[float, float],
    v0: tuple[float, float],
    t0: float,
    target: Body,
    candidates: list[Candidate],
    *,
    leaving: Body | None,
    floor_radii: float,
) -> list[Shot | None]:
    """Every candidate flown out of its periapsis both ways and moved until the
    lines join the passage's two ends -- one batch, all candidates together."""
    count = len(candidates)
    if not count:
        return []
    #: The ends are joined at the worlds' centres, as the conics price them:
    #: their own pull is in `escape`, not in the line.
    sky2 = dataclasses.replace(
        system,
        bodies=tuple(
            one
            for one in system.bodies
            if one.key != target.key and (leaving is None or one.key != leaving.key)
        ),
    )
    vias = [system.body(one.via) for one in candidates]
    floors = np.array([floor_radii * one.radius for one in vias])
    start = np.asarray(r0, dtype=float)
    ends = np.array([t0 + one.hours / HOURS_PER_DAY for one in candidates])
    goal_r, goal_v = place(target, ends)
    guess = np.zeros((count, 5))
    senses = np.zeros(count)
    for i, one in enumerate(candidates):
        guess[i], senses[i] = seed_of(vias[i], one, t0)
    pinned = guess[:, 4] <= floors
    guess[:, 4] = np.maximum(guess[:, 4], floors)

    def fly(rows: np.ndarray, u: np.ndarray) -> tuple[np.ndarray, Rows, Rows, np.ndarray]:
        """Out of each row's periapsis both ways: the misses at both ends, the
        velocities there, and whether the lines kept off the ground."""
        n = len(rows)
        theta, moment, speed_in, speed_out, radius = u.T
        via_r = np.zeros((n, 2))
        via_v = np.zeros((n, 2))
        for k, row in enumerate(rows):
            p, vp = place(vias[row], moment[k])
            via_r[k], via_v[k] = p[0], vp[0]
        outward = np.stack([np.cos(theta), np.sin(theta)], axis=1)
        along = senses[rows][:, None] * np.stack([-np.sin(theta), np.cos(theta)], axis=1)
        peri = via_r + radius[:, None] * outward
        clear = np.ones(2 * n, dtype=bool)

        def watch(tt: np.ndarray, rr: Rows, vv: Rows) -> np.ndarray:
            hit = np.hypot(rr[:, 0], rr[:, 1]) < sky2.corona
            for body in sky2.bodies:
                p, _ = place(body, tt)
                hit |= np.hypot(*(rr - p).T) < body.radius
            clear[hit] = False
            return ~clear

        rr, vv = advance(
            sky2,
            np.concatenate([moment, moment]),
            np.concatenate([np.full(n, t0), ends[rows]]),
            np.concatenate([peri, peri]),
            np.concatenate([via_v + speed_in[:, None] * along, via_v + speed_out[:, None] * along]),
            dt_max=_OPEN,
            watch=watch,
        )
        miss = np.concatenate([rr[:n] - start, rr[n:] - goal_r[rows]], axis=1)
        return miss, vv[:n], vv[n:], clear[:n] & clear[n:]

    miss = np.full(count, np.inf)
    alive = np.ones(count, dtype=bool)
    for _ in range(_ITERATIONS):
        rows = np.flatnonzero(alive & (miss > _MISS))
        if not len(rows):
            break
        nudged = np.repeat(guess[rows], 6, axis=0)
        for k in range(5):
            nudged[k + 1 :: 6, k] += _NUDGE[k]
        errors, _, _, clear = fly(np.repeat(rows, 6), nudged)
        errors = errors.reshape(len(rows), 6, 4)
        miss[rows] = np.linalg.norm(errors[:, 0], axis=1)
        alive[rows] &= clear.reshape(len(rows), 6)[:, 0]
        jacobian = (errors[:, 1:] - errors[:, :1]).transpose(0, 2, 1) / _NUDGE
        steps = np.zeros((len(rows), 5))
        for k, row in enumerate(rows):
            if not alive[row] or miss[row] <= _MISS:
                continue
            width = 4 if pinned[row] else 5
            steps[k, :width] = np.linalg.lstsq(jacobian[k, :, :width], -errors[k, 0], rcond=None)[0]
        reach = np.concatenate(
            [np.tile(_REACH, (len(rows), 1)), _REACH_RADIUS * guess[rows, 4:5]], axis=1
        )
        scale = 1.0 / np.maximum(np.max(np.abs(steps) / reach, axis=1), 1.0)
        todo = np.flatnonzero(alive[rows] & (miss[rows] > _MISS))
        for _halving in range(_HALVINGS):
            if not len(todo):
                break
            ids = rows[todo]
            trial = guess[ids] + steps[todo] * scale[todo, None]
            low = trial[:, 4] < floors[ids]
            trial[low, 4] = floors[ids][low]
            errs, _, _, ok = fly(ids, trial)
            better = ok & (np.linalg.norm(errs, axis=1) < miss[ids])
            guess[ids[better]] = trial[better]
            miss[ids[better]] = np.linalg.norm(errs[better], axis=1)
            pinned[ids[better & low]] = True
            scale[todo[~better]] *= 0.5
            todo = todo[~better]
        #: A candidate no step of which helped has nowhere left to go.
        alive[rows[todo]] = False
    errors, back, onward, clear = fly(np.arange(count), guess)
    miss = np.linalg.norm(errors, axis=1)
    base = place(leaving, t0)[1][0] if leaving is not None else np.asarray(v0, dtype=float)
    shots: list[Shot | None] = []
    for i, one in enumerate(candidates):
        theta, moment, speed_in, speed_out, radius = guess[i]
        if not (
            clear[i]
            and t0 < moment < ends[i]
            and miss[i] < _OFFER_MISS
            and radius >= floors[i] * (1 - 1e-6)
            and speed_in > 0
            and speed_out > 0
        ):
            shots.append(None)
            continue
        v1 = back[i]
        excess = float(np.hypot(*(v1 - base)))
        dv_out = (
            float(escape(leaving, park_of(system, leaving), np.array([excess]))[0])
            if leaving is not None
            else excess
        )
        dv_in = float(
            escape(target, park_of(system, target), np.array([np.hypot(*(onward[i] - goal_v[i]))]))[
                0
            ]
        )
        dv_pass = float(abs(speed_out - speed_in))
        aim = astro.propagate(system.mu, r0, (float(v1[0]), float(v1[1])), moment - t0)[0]
        via_at = place(vias[i], moment)[0][0]
        shots.append(
            Shot(
                candidate=one,
                at=float(moment - t0),
                rp=float(senses[i] * radius),
                speed_in=float(speed_in),
                speed_out=float(speed_out),
                v1=(float(v1[0]), float(v1[1])),
                aim=(float(aim[0] - via_at[0]), float(aim[1] - via_at[1])),
                dv_out=dv_out,
                dv_pass=dv_pass,
                dv_in=dv_in,
                dv=dv_out + dv_pass + dv_in,
            )
        )
    return shots


def periapsis(mu: float, rel: Rows, v_rel: Rows) -> tuple[np.ndarray, np.ndarray]:
    """The osculating periapsis of every row round a world: its radius signed
    with the sense of the motion, and the days until it (negative once past)."""
    gap = np.hypot(rel[:, 0], rel[:, 1])
    speed2 = np.sum(v_rel * v_rel, axis=1)
    momentum = rel[:, 0] * v_rel[:, 1] - rel[:, 1] * v_rel[:, 0]
    energy = speed2 / 2.0 - mu / gap
    shape = np.sqrt(np.maximum(0.0, 1.0 + 2.0 * energy * momentum * momentum / (mu * mu)))
    radius = momentum * momentum / (mu * (1.0 + shape))
    radial = np.sign(np.sum(rel * v_rel, axis=1))
    axis = np.abs(mu / (2.0 * np.where(np.abs(energy) > 1e-12, energy, 1e-12)))
    open_ = energy > 0
    hyper = np.arccosh(np.maximum((1.0 + gap / axis) / np.maximum(shape, 1e-12), 1.0))
    ellip = np.arccos(np.clip((1.0 - gap / axis) / np.maximum(shape, 1e-12), -1.0, 1.0))
    mean = np.where(open_, shape * np.sinh(hyper) - hyper, ellip - shape * np.sin(ellip)) * radial
    until = -mean / np.sqrt(mu / axis**3)
    return np.sign(momentum) * radius, until


def _to_pass(
    system: System,
    t: float,
    r: tuple[float, float],
    rows: Rows,
    via: Body,
    until: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Coast each row of velocities from `(t, r)` and read the periapsis of its
    pass by `via`: signed radius, moment, and whether there was a pass."""
    n = len(rows)
    zone = sphere(system, via)
    seen = np.zeros(n, dtype=bool)
    radius = np.full(n, np.nan)
    moment = np.full(n, np.nan)

    def watch(tt: np.ndarray, rr: Rows, vv: Rows) -> np.ndarray:
        p, vp = place(via, tt)
        rel, v_rel = rr - p, vv - vp
        gap = np.hypot(rel[:, 0], rel[:, 1])
        now = ~seen & (gap < zone) & (np.sum(rel * v_rel, axis=1) >= 0)
        if np.any(now):
            signed, left = periapsis(via.mu, rel[now], v_rel[now])
            radius[now], moment[now] = signed, tt[now] + left
            seen[now] = True
        return seen

    advance(
        system,
        np.full(n, t),
        np.full(n, until),
        np.repeat(np.asarray([r], dtype=float), n, axis=0),
        rows,
        dt_max=_OPEN,
        watch=watch,
    )
    return radius, moment, seen


def pass_fix(
    system: System,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
    via: Body,
    rp: float,
    at: float,
) -> tuple[float, float] | None:
    """The burn now that puts the pass by `via` at signed periapsis `rp` at
    sky day `at` -- or nothing, where the coast from here passes nowhere near
    it or the solve does not close."""
    speed = max(float(np.hypot(*v)), 1.0)
    until = at + max(0.3, 0.3 * (at - t))
    here = np.asarray(v, dtype=float)
    burn = np.zeros(2)

    def errors(x: np.ndarray) -> np.ndarray | None:
        rows = np.array([here + x, here + x + [_FIX_NUDGE, 0.0], here + x + [0.0, _FIX_NUDGE]])
        radius, moment, seen = _to_pass(system, t, r, rows, via, until)
        if not seen.all():
            return None
        return np.stack([radius - rp, (moment - at) * speed], axis=1)

    err = errors(burn)
    if err is None:
        return None
    for _ in range(_FIX_ITERATIONS):
        if abs(err[0, 0]) < _FIX_RADIUS and abs(err[0, 1]) < _FIX_MINUTE * speed:
            return (float(burn[0]), float(burn[1]))
        jacobian = np.column_stack([(err[1] - err[0]), (err[2] - err[0])]) / _FIX_NUDGE
        try:
            step = np.linalg.solve(jacobian, -err[0])
        except np.linalg.LinAlgError:
            break
        step *= min(1.0, _FIX_REACH / max(float(np.hypot(*step)), 1e-12))
        merit = float(np.hypot(*err[0]))
        for _halving in range(_FIX_HALVINGS):
            trial = errors(burn + step)
            if trial is not None and float(np.hypot(*trial[0])) < merit:
                burn, err = burn + step, trial
                break
            step *= 0.5
        else:
            break
    #: Short of the tolerance but better than doing nothing: the next
    #: correction starts from nearer.
    return (float(burn[0]), float(burn[1])) if float(np.hypot(*burn)) > 0.0 else None


def arrival_fix(
    system: System,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
    target: Body,
    arrive: float,
    *,
    seed: tuple[float, float],
) -> tuple[tuple[float, float], float]:
    """The burn now that ends the coast at the target's place at `arrive`, and
    how far it still misses. Flown without the target's own pull: the line
    ends at its centre, and the capture near it is the helm's (`guide`)."""
    sky2 = dataclasses.replace(
        system, bodies=tuple(one for one in system.bodies if one.key != target.key)
    )
    goal = place(target, arrive)[0][0]
    here = np.asarray(v, dtype=float)

    def ends(x: np.ndarray) -> np.ndarray:
        rows = np.array([here + x, here + x + [_FIX_NUDGE, 0.0], here + x + [0.0, _FIX_NUDGE]])
        rr, _ = advance(
            sky2,
            np.full(3, t),
            np.full(3, arrive),
            np.repeat(np.asarray([r], dtype=float), 3, axis=0),
            rows,
            dt_max=_OPEN,
        )
        return rr - goal

    burn = np.asarray(seed, dtype=float)
    err = ends(burn)
    for _ in range(_FIX_ITERATIONS):
        merit = float(np.hypot(*err[0]))
        if merit < _MISS:
            break
        jacobian = np.column_stack([(err[1] - err[0]), (err[2] - err[0])]) / _FIX_NUDGE
        try:
            step = np.linalg.solve(jacobian, -err[0])
        except np.linalg.LinAlgError:
            break
        step *= min(1.0, _ARRIVAL_REACH / max(float(np.hypot(*step)), 1e-12))
        for _halving in range(_FIX_HALVINGS):
            trial = ends(burn + step)
            if float(np.hypot(*trial[0])) < merit:
                burn, err = burn + step, trial
                break
            step *= 0.5
        else:
            break
    return (float(burn[0]), float(burn[1])), float(np.hypot(*err[0]))
