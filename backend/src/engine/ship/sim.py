# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: the hull flown by the sky (D-289).

The floor above `src.sky`, and the one that owns rows: the state on the
ship's row, the order the autopilot flies, the tick that moves both, the
coast a dry hull is left on, and the hour that coast ends.

**Three kinds of hull in space.** Moored to an orbital node, a hull runs on
the parking circle -- analytic, nothing to integrate, `park_phase` and the
stamp say where on it. Under an order, it is flown by the tick: every step
the helm re-solves the passage from where the hull actually is and burns
what the thrust allows, the tanks paying as the engines go. Adrift -- dry,
with no order -- it coasts: the state is propagated on reading and its stamp
moved along every `orbit.restamp_hours`, and the forecast's hour for its
end is a job in the journal.

**What a tick costs.** Only hulls under an order are stepped every minute;
a coasting hull costs a step every few hours, a moored one nothing. The
helm asks the sky one Lambert solution a step.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import OrderedDict
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from src import sky
from src.constants import Catalog, Constants, current
from src.constants import registry as R
from src.db.base import remember
from src.engine import stock, travel
from src.engine.ship import course
from src.engine.ship._base import (
    NoArc,
    NoFuel,
    NotEnoughThrust,
    _gangway_seconds,
    is_orbit,
)
from src.engine.ship.building import moor_to
from src.engine.ship.physics import (
    efficiency,
    engine_class,
    fuel_energy,
    fuel_stacks,
    mass,
    orbits_of,
    sky_days,
)
from src.models.ship import Ship
from src.models.world import Node, Surface
from src.units import (
    HOURS_PER_DAY,
    KG_PER_TON,
    MINUTES_PER_HOUR,
    ROUND_DV,
    ROUND_HOURS,
    ROUND_TRACE,
    SKY_CURVE_MEMO,
    SKY_MEMO_PER_DAY,
    amount_float,
)

#: What the journal names as the cause of a death by the sky (D-251): a
#: payload key, never a sentence.
CRASHED = "crash"
LOST = "lost"

_DV_EPS = sky.DV_EPS


async def system(session: AsyncSession, constants: Constants) -> sky.System:
    """The sky as the vault and the seed describe it. One reading per command."""
    return await remember(
        session,
        ("sky.system", constants.digest),
        lambda: _system(session, constants),
    )


async def _system(session: AsyncSession, constants: Constants) -> sky.System:
    return sky.system_of(constants, await orbits_of(session))


def _stamp(moment: datetime) -> str:
    return moment.isoformat()


# --- where a hull is ---------------------------------------------------------


async def state_at(
    session: AsyncSession, constants: Constants, ship: Ship, *, now: datetime
) -> tuple[tuple[float, float], tuple[float, float], float] | None:
    """The hull's place and speed at `now`, and the sky day of it -- or nothing
    for a hull that is not in the sky at all (at a spaceport, on a climb).

    A read: the circle is arithmetic, the coast is propagated from the
    stamp, and neither is written back here.
    """
    if ship.sky_at is None or ship.lost_at is not None:
        return None
    #: On the hold (D-289, wave 3) the hull flies as one with the hull it came
    #: to rest beside: its place is that hull's, and only that hull's row is
    #: moved by the tick.
    if ship.held_ship_id is not None:
        other = await session.get(Ship, ship.held_ship_id)
        if other is not None and other.lost_at is None and other.held_ship_id is None:
            return await state_at(session, constants, other, now=now)
    world = await system(session, constants)
    t = await sky_days(session, now)
    if ship.docked_node_id is not None:
        moored = await session.get(Node, ship.docked_node_id)
        if moored is None or not is_orbit(moored):  # pragma: no cover -- a stale stamp
            return None
        try:
            body = world.body(moored.planet.value)
        except KeyError:
            #: An orbit over a planet the sky does not run: a world laid
            #: without spheres. No circle to be on.
            return None
        t0 = await sky_days(session, ship.sky_at)
        phase = float(ship.park_phase or 0.0) + sky.circle_rate(body, world.park) * (t - t0)
        r, v = sky.parking(world, body, t, phase)
        return _row(r), _row(v), t
    r0, v0 = _state_of(ship)
    t0 = await sky_days(session, ship.sky_at)
    if t <= t0:
        return r0, v0, t0
    step = float(constants[R.ORBIT_PLAN_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY
    r, v = sky.advance(
        world, np.array([t0]), np.array([t]), np.array([r0]), np.array([v0]), dt_max=step
    )
    return _row(r), _row(v), t


async def forecast_of(session: AsyncSession, ship: Ship) -> dict[str, Any] | None:
    """The coast ahead as the tick last wrote it: the hull's own, or, on the
    hold, the reference hull's -- the pair flies as one."""
    if ship.held_ship_id is not None:
        other = await session.get(Ship, ship.held_ship_id)
        if other is not None and other.lost_at is None:
            return other.forecast or None
    return ship.forecast or None


async def drifter_of(
    session: AsyncSession, constants: Constants, other: Ship
) -> sky.Drifter | None:
    """Another hull as a target (D-289, wave 3): its forecast as the line a
    rendezvous is aimed at, or nothing while the tick has not counted one."""
    stored = await forecast_of(session, other)
    if stored is None or len(stored.get("trace") or ()) <= 1:
        return None
    since = datetime.fromisoformat(str(stored["since"]))
    #: How long the line is: the coast's end, or one lap of a bound ellipse
    #: read modulo its period. `at` is the verdict's hour and, for a lap,
    #: the horizon -- a line stretched to it aimed the slider at a phantom.
    until = datetime.fromisoformat(str(stored.get("until") or stored["at"]))
    return sky.Drifter(
        key=f"ship:{other.id}:{stored['since']}",
        t0=await sky_days(session, since),
        t1=await sky_days(session, until),
        trace=tuple((float(x), float(y)) for x, y in stored["trace"]),
        loops=bool(stored.get("loops")),
    )


async def states_at(
    session: AsyncSession, constants: Constants, ships: Sequence[Ship], *, now: datetime
) -> dict[uuid.UUID, tuple[tuple[float, float], tuple[float, float]]]:
    """Where every one of `ships` is at `now`, in one pass: the moored on
    their circles, the coasting flown from their stamps as one batch of the
    integrator, the held at their references. What the sighting and the
    console's list of others read -- one propagation per hull per tick, not
    one per pair.
    """
    world = await system(session, constants)
    t = await sky_days(session, now)
    found: dict[uuid.UUID, tuple[tuple[float, float], tuple[float, float]]] = {}
    coasting: list[Ship] = []
    for one in ships:
        if one.sky_at is None or one.lost_at is not None or one.held_ship_id is not None:
            continue
        if one.docked_node_id is not None:
            state = await state_at(session, constants, one, now=now)
            if state is not None:
                found[one.id] = (state[0], state[1])
            continue
        coasting.append(one)
    if coasting:
        step = float(constants[R.ORBIT_PLAN_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY
        starts = np.array([await sky_days(session, one.sky_at) for one in coasting])
        r0 = np.array([_state_of(one)[0] for one in coasting], dtype=float)
        v0 = np.array([_state_of(one)[1] for one in coasting], dtype=float)
        #: Never backwards: a stamp ahead of the clock is read as it stands.
        rr, vv = sky.advance(world, starts, np.maximum(starts, t), r0, v0, dt_max=step)
        for one, r, v in zip(coasting, rr, vv, strict=True):
            found[one.id] = ((float(r[0]), float(r[1])), (float(v[0]), float(v[1])))
    for one in ships:
        if one.held_ship_id is not None and one.held_ship_id in found:
            found[one.id] = found[one.held_ship_id]
    return found


def _state_of(ship: Ship) -> tuple[tuple[float, float], tuple[float, float]]:
    return (float(ship.sky_x or 0.0), float(ship.sky_y or 0.0)), (
        float(ship.sky_vx or 0.0),
        float(ship.sky_vy or 0.0),
    )


def _row(rows: np.ndarray) -> tuple[float, float]:
    return float(rows[0, 0]), float(rows[0, 1])


def _write_state(
    ship: Ship, r: tuple[float, float], v: tuple[float, float], *, at: datetime
) -> None:
    ship.sky_x, ship.sky_y = r
    ship.sky_vx, ship.sky_vy = v
    ship.sky_at = at


async def moor(
    session: AsyncSession, ship: Ship, orbit: Node, *, now: datetime, phase: float
) -> None:
    """Put the hull on the parking circle of this orbital node (D-289).

    The mooring itself is D-245's: the one edge to the node, the berth, the
    planet the rooms take. What the sky adds is the circle: the phase the
    hull sits at and the moment it was put there, from which the circle is
    arithmetic.
    """
    ship.berth = 1
    await travel.connect(
        session,
        orbit,
        await session.get(Node, ship.connector_node_id),
        base_seconds=_gangway_seconds(await _constants(session), ship.berth),
        surface=Surface.PAVED,
    )
    ship.docked_node_id = orbit.id
    await moor_to(session, ship, orbit)
    ship.park_phase = phase
    ship.course = None
    #: A moored hull carries no coast ahead: its circle the chart draws.
    ship.forecast = None
    ship.sky_at = now
    ship.sky_x = ship.sky_y = ship.sky_vx = ship.sky_vy = None
    await session.flush()


async def _constants(session: AsyncSession) -> Constants:
    return current()


def bearing_of(ship: Ship) -> float:
    """Where on the circle a hull arriving from a climb is put: spun off its
    id, so two hulls over one planet do not sit at one point."""
    return sky.bearing(ship.id.hex)


# --- the order ------------------------------------------------------------------


def dv_aboard(constants: Constants, worth: float, weight: float, klass: int | None) -> float:
    """What speed the tanks buy at this mass, units a day (D-289): the
    console's number, and the tick's budget."""
    per_unit = float(constants[R.SHIP_FUEL_PER_TON_SPEED]) * weight / KG_PER_TON
    per_unit *= efficiency(constants, klass)
    return worth / per_unit if per_unit > 0 else 0.0


def fuel_for_dv(constants: Constants, weight: float, dv: float, klass: int | None) -> float:
    """The reference units of fuel a delta-v costs at this mass (D-271, D-252)."""
    return course.fuel_for_speed(constants, weight, dv, efficiency=efficiency(constants, klass))


async def offers(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    target: sky.Target,
    *,
    now: datetime,
    thrust_ratio: float,
) -> list[sky.Sample]:
    """The slider from where the hull is: the preview for every point of the
    grid (D-271, D-289) -- to a planet's circle, or to a drifter on its
    forecast (wave 3). Empty for a hull not in the sky."""
    found = await state_at(session, constants, ship, now=now)
    if found is None:
        return []
    r, v, t = found
    world = await system(session, constants)
    if isinstance(target, sky.Drifter):
        #: A hull as the target (wave 3): one price, the approach profile's
        #: own -- the helm flies that profile and no arc, so a slider of
        #: arcs would quote hours and delta-v nobody flies.
        a_max = thrust_ratio * float(constants[R.ORBIT_THRUST_SCALE])
        return [sky.approach_quote(r, v, t, target, a_max)]
    leaving = None
    if ship.docked_node_id is not None:
        moored = await session.get(Node, ship.docked_node_id)
        if moored is not None and is_orbit(moored):
            leaving = world.body(moored.planet.value)
    #: Memoised on the state, rounded as the wire rounds it, and on the
    #: sky's own minute: every console over a planet asks the same question
    #: of the same sky, and forty Lambert solutions a planet on every reread
    #: of `ship.view` were a tenth of a second each in the event loop. What
    #: is not remembered is solved off the loop.
    key = (
        constants.digest,
        target.key,
        None if leaving is None else leaving.key,
        round(t * SKY_MEMO_PER_DAY),
        round(r[0], ROUND_TRACE),
        round(r[1], ROUND_TRACE),
        round(v[0], ROUND_DV),
        round(v[1], ROUND_DV),
    )
    hit = _PREVIEWS.get(key)
    if hit is None:
        hit = await asyncio.to_thread(
            sky.preview, world, constants, r, v, t, target, course.grid(constants), leaving=leaving
        )
        _PREVIEWS[key] = hit
        while len(_PREVIEWS) > SKY_CURVE_MEMO:
            _PREVIEWS.popitem(last=False)
    return list(hit)


#: The slider previews remembered across commands (see `offers`).
_PREVIEWS: OrderedDict[tuple, list[sky.Sample]] = OrderedDict()


async def depart(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    target: Node | Ship,
    *,
    hours: float,
    thrust_ratio: float,
    now: datetime,
    offered: Sequence[sky.Sample] | None = None,
) -> tuple[sky.Sample, float]:
    """Set the order: the chosen point of the slider, written onto the row.
    Returns the plan -- the slider's own sample -- and the fuel it will cost
    by the plan's delta-v.

    The plan is the two-body arc the slider showed, no more: a shooting
    refinement under five bodies was tried and dropped (D-289, wave 2) --
    it diverged on the cheap end of the slider and bought only a picture,
    because the helm re-solves the passage from where the hull actually is
    every tick (`_fly`) whatever line was drawn at the order.

    Refused for what is impossible **now** and for nothing else (D-289):
    an arc the sky does not offer, one the engines cannot deliver, or
    tanks that do not hold the departure burn. The arrival burn is the
    console's warning, not the engine's refusal -- fuel may be made on the
    way, and a hull short of it drifts rather than being kept at the pier.
    """
    world = await system(session, constants)
    goal: sky.Target
    if isinstance(target, Ship):
        #: A hull as the target (wave 3): met on its forecast, the line the
        #: tick wrote on its row. Without one there is nothing to aim at yet.
        found_goal = await drifter_of(session, constants, target)
        if found_goal is None:
            raise NoArc(key="ship-target-unknown")
        goal = found_goal
    else:
        goal = world.body(target.planet.value)
    if offered is None:
        offered = await offers(
            session, constants, catalog, ship, goal, now=now, thrust_ratio=thrust_ratio
        )
    if isinstance(goal, sky.Drifter):
        #: One price to a hull and no choice among prices: the quote of the
        #: order's own moment, whatever hours the console read minutes ago
        #: -- the profile's hours move with the geometry, and the profile
        #: is laid within the thrust by construction.
        (sample,) = offered
        hours = sample.hours
        if gone_by(goal, await sky_days(session, now), hours):
            raise NoArc(key="ship-target-gone-by-then", other=target.name)
    else:
        samples = {one.hours: one for one in offered}
        found_sample = samples.get(round(hours, ROUND_HOURS)) or samples.get(hours)
        if found_sample is None:
            raise NoArc(key="ship-no-arc", hours=round(hours, ROUND_HOURS))
        sample = found_sample
        can = course.deliverable(constants, thrust_ratio, hours)
        if sample.dv > can:
            raise NotEnoughThrust(
                key="ship-too-fast-for-thrust",
                hours=round(hours, ROUND_HOURS),
                need=round(sample.dv, ROUND_DV),
                have=round(can, ROUND_DV),
            )
    found = await state_at(session, constants, ship, now=now)
    if found is None:  # pragma: no cover -- `offers` answered, so the hull is in the sky
        raise NoArc(key="ship-no-arc", hours=round(hours, ROUND_HOURS))
    r, v, _ = found
    plan = sample

    weight = await mass(session, constants, catalog, ship)
    klass = await engine_class(session, constants, ship)
    stacks = await stock.lock_items(
        session, await fuel_stacks(session, constants, catalog, ship), ordered=True
    )
    worth = sum(amount_float(one.amount) * fuel_energy(constants, one.type_key) for one in stacks)
    need = fuel_for_dv(constants, weight, plan.dv_out, klass)
    if worth + _DV_EPS < need:
        raise NoFuel(key="ship-no-fuel", why="cross", need=need, goods="ship_fuel", have=worth)

    #: Whoever was holding on to this hull was let go of by the caller
    #: (`hold.release_holders`), from the state they shared; the hull's own
    #: state is written here, and the hold it may itself have been on is
    #: over (wave 3).
    _write_state(ship, r, v, at=now)
    ship.park_phase = None
    ship.held_ship_id = None
    ship.course = {
        "target": None if isinstance(target, Ship) else target.key,
        "planet": None if isinstance(target, Ship) else target.planet.value,
        "ship": str(target.id) if isinstance(target, Ship) else None,
        "since": _stamp(now),
        #: The hour the helm aims at, and the hour the console promises: the
        #: plan's burns are instants, the hull's are stretches, and braking
        #: from the arc's speed at this thrust puts the hull on the circle
        #: later than the arc reaches the planet (`sky.brake_days`).
        "arrive_at": _stamp(now + timedelta(hours=hours)),
        "due_at": _stamp(
            now
            + timedelta(hours=hours)
            + timedelta(
                days=sky.brake_days(
                    plan.dv_in, thrust_ratio * float(constants[R.ORBIT_THRUST_SCALE])
                )
            )
        ),
        "hours": round(hours, ROUND_HOURS),
        "dv": round(plan.dv, ROUND_DV),
        "dv_out": round(plan.dv_out, ROUND_DV),
        "dv_in": round(plan.dv_in, ROUND_DV),
        "trace": [[round(x, ROUND_TRACE), round(y, ROUND_TRACE)] for x, y in plan.trace],
        "phase": sky.BURN,
        #: What the engines have burnt of it so far, units a day: the console
        #: reads what is left against the tanks (`card.profile`).
        "spent": 0.0,
    }
    #: A fresh order has no forecast yet: the tick writes one within the
    #: minute, and the console draws nothing rather than yesterday's coast.
    ship.forecast = None
    await session.flush()
    return plan, fuel_for_dv(constants, weight, plan.dv, klass)


def meetable(other: Ship) -> bool:
    """Whether a hull can be met: coasting in the sky, on nobody's hold."""
    return (
        other.lost_at is None
        and other.docked_node_id is None
        and other.sky_at is not None
        and not other.course
        and other.held_ship_id is None
    )


def unmeetable(rows: Any) -> ColumnElement[bool]:
    """`not meetable`, said in SQL over `rows` (the `Ship` table or an alias
    of it): the one predicate kept next to the other, so the tick's sweep
    and the Python check cannot drift apart."""
    return or_(
        rows.lost_at.isnot(None),
        rows.docked_node_id.isnot(None),
        rows.sky_at.is_(None),
        rows.course.isnot(None),
        rows.held_ship_id.isnot(None),
    )


def gone_by(target: sky.Drifter, t0: float, hours: float) -> bool:
    """Whether the target's line ends before `hours` from `t0`: a coast that
    comes down or leaves is a line with an end, and a meeting past it is a
    meeting with a hull that is no longer there. A lap has no end."""
    return not target.loops and t0 + hours / HOURS_PER_DAY > target.t1


async def part_hulls(session: AsyncSession, constants: Constants, one: Ship, other: Ship) -> None:
    """The gangway between two hulls taken away (wave 3), if it stands: the
    edge goes with the docking mark, whoever clears the mark."""
    mine = await session.get(Node, one.connector_node_id)
    theirs = await session.get(Node, other.connector_node_id)
    if mine is None or theirs is None:
        return
    #: `disconnect` answers False for no edge: the check is the removal.
    await travel.disconnect(session, mine, theirs)


# --- what the console reads --------------------------------------------------------


async def picture(
    session: AsyncSession, constants: Constants, catalog: Catalog, ship: Ship, *, now: datetime
) -> dict[str, object] | None:
    """The hull in the sky for the console (D-289): where it is, and where
    inertia takes it. Nothing for a hull not in the sky -- and nothing for a
    moored one either: its circle the client draws, and a ninety-day forecast
    of a circle is arithmetic nobody reads.

    A read, and a cheap one: the place is propagated from the stamp, and the
    coast ahead is what the tick last wrote onto the row (`forecast`). Flying
    it here, on every reread of the console and on the public map, was the
    review's critical finding -- seconds of five-body arithmetic in the event
    loop per hull -- and it is not a reading's job.
    """
    if ship.docked_node_id is not None:
        return None
    found = await state_at(session, constants, ship, now=now)
    if found is None:
        return None
    r, _, _ = found
    stored = await forecast_of(session, ship)
    return {
        "x": round(r[0], ROUND_TRACE),
        "y": round(r[1], ROUND_TRACE),
        "at": _stamp(now),
        #: The tick's forecast, or nothing while the first tick since the
        #: order is still to come: the chart then draws no coast at all.
        "inertia": (
            None
            if stored is None
            else {
                "kind": stored["kind"],
                "at": stored["at"],
                "body": stored.get("body"),
                "trace": stored.get("trace") or [],
            }
        ),
    }


def _keep_forecast(ship: Ship, fate: sky.Fate, *, now: datetime, t: float) -> None:
    """Write the coast ahead onto the row: the verdict, its hour, the line to
    draw, and the moment it was counted from -- so the next tick knows when
    it has aged (`_forecast_stale`)."""
    ship.forecast = {
        "kind": fate.kind,
        "at": _stamp(now + timedelta(days=fate.at - t)),
        "body": fate.body,
        "trace": [[round(x, ROUND_TRACE), round(y, ROUND_TRACE)] for x, y in fate.trace],
        "since": _stamp(now),
        #: The line's own length, and whether it is a lap read round and
        #: round: what a rendezvous is aimed along (`drifter_of`).
        "until": _stamp(now + timedelta(days=fate.span)),
        "loops": fate.loops,
    }


def _forecast_stale(constants: Constants, ship: Ship, now: datetime) -> bool:
    """Whether the forecast on the row is older than the coaster's cadence."""
    stored = ship.forecast or None
    if stored is None:
        return True
    since = datetime.fromisoformat(str(stored.get("since")))
    return now - since >= timedelta(hours=float(constants[R.ORBIT_RESTAMP_HOURS]))
