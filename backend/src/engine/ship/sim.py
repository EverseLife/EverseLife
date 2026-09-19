# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: the hull flown by the sky (D-289).

The floor above `src.sky`, and the one that owns rows: the state on the
ship's row, the order the autopilot flies, the tick that moves both, the
coast a dry hull is left on, and the hour that coast ends.

**Two kinds of hull in space** (D-354: there is no node above a planet to
moor to any more). Under an order, a hull is flown by the tick: every step
the helm re-solves the passage from where the hull actually is and burns
what the thrust allows, the tanks paying as the engines go. With no order
it coasts: the state is propagated on reading and its stamp moved along
every `orbit.restamp_hours`, and the forecast's hour for its end, if it
ends, is a job in the journal. A coast on a closed orbit round a planet is
the hull "in orbit" (`orbiting`); close in, it is read by Kepler round the
planet between restamps; every stamp is written from the whole sky, so what
the star's tide does to an orbit over weeks is the integrator's to say.

**What a tick costs.** Only hulls under an order are stepped every minute;
a coasting hull costs a restamp every few hours, and a reading in between
is arithmetic where Kepler reads it. The
helm asks the sky one Lambert solution a step -- under a flyby (D-341) only
while it leaves and arrives: between, it coasts and asks for a correction
whenever the time left has halved.
"""

from __future__ import annotations

import uuid
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
from src.engine.ship import course, flyby
from src.engine.ship._base import (
    InFlight,
    NoArc,
    NoFuel,
)
from src.engine.ship.building import hang_over
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
from src.models.world import Node
from src.units import (
    HOURS_PER_DAY,
    KG_PER_TON,
    MINUTES_PER_HOUR,
    ROUND_DV,
    ROUND_HOURS,
    ROUND_NEAR,
    ROUND_TRACE,
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
    session: AsyncSession,
    constants: Constants,
    ship: Ship,
    *,
    now: datetime,
    exact: bool = False,
) -> tuple[tuple[float, float], tuple[float, float], float] | None:
    """The hull's place and speed at `now`, and the sky day of it -- or nothing
    for a hull that is not in the sky at all (at a spaceport, on a climb).

    A read: the coast is propagated from the stamp, and nothing is written
    back here. A coast on a closed orbit close
    round a planet is read by Kepler round it (`sky.kepler_reads`, D-354):
    between two restamps the tides Kepler leaves out move it by under a
    hundredth of the distance two hulls meet at -- and further, in step with
    the stamp's age, if the tick falls behind. `exact` is for whoever writes
    a stamp from the state -- the restamp, an order, a hold, a loss: it flies
    the whole sky, so no stamp is ever a Kepler reading.
    """
    if ship.sky_at is None or ship.lost_at is not None or ship.docked_node_id is not None:
        return None
    #: On the hold (D-289, wave 3) the hull flies as one with the hull it came
    #: to rest beside: its place is that hull's, and only that hull's row is
    #: moved by the tick.
    if ship.held_ship_id is not None:
        other = await session.get(Ship, ship.held_ship_id)
        if other is not None and other.lost_at is None and other.held_ship_id is None:
            return await state_at(session, constants, other, now=now, exact=exact)
    world = await system(session, constants)
    t = await sky_days(session, now)
    r0, v0 = _state_of(ship)
    t0 = await sky_days(session, ship.sky_at)
    #: Either way in time: the sky is flown backwards as well as forwards, and
    #: a lap is a lap -- so a stamp the clock has not reached yet (a leg's
    #: arrival, stamped at its hour) is read where the hull is now, not where
    #: it will be. Read as it stood, it put a hull a planet's hours of motion
    #: off its planet the moment an order was written from it.
    if t == t0:
        return r0, v0, t0
    held = None if exact else sky.bound_to(world, t0, r0, v0)
    if held is not None and sky.kepler_reads(world, held, _window(constants)):
        r, v = sky.bound_states([held], t)
        return _row(r), _row(v), t
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
    around = stored.get("around")
    world = await system(session, constants)
    return sky.Drifter(
        key=f"ship:{other.id}:{stored['since']}",
        t0=await sky_days(session, since),
        t1=await sky_days(session, until),
        trace=tuple((float(x), float(y)) for x, y in stored["trace"]),
        loops=bool(stored.get("loops")),
        around=None if around is None else _body_or_none(world, str(around)),
    )


def _body_or_none(world: sky.System, key: str) -> sky.Body | None:
    try:
        return world.body(key)
    except KeyError:  # pragma: no cover -- a lap is counted round a planet the sky runs
        return None


async def states_at(
    session: AsyncSession, constants: Constants, ships: Sequence[Ship], *, now: datetime
) -> dict[uuid.UUID, tuple[tuple[float, float], tuple[float, float]]]:
    """Where every one of `ships` is at `now`, in one pass: the hulls close
    round a planet on their orbits, the rest flown from their stamps as one
    batch of the integrator, the held at their references. What the sighting and the
    console's list of others read -- one propagation per hull per tick, not
    one per pair.
    """
    world = await system(session, constants)
    t = await sky_days(session, now)
    found: dict[uuid.UUID, tuple[tuple[float, float], tuple[float, float]]] = {}
    coasting: list[Ship] = []
    for one in ships:
        if (
            one.sky_at is None
            or one.lost_at is not None
            or one.held_ship_id is not None
            or one.docked_node_id is not None
        ):
            continue
        coasting.append(one)
    #: On a closed orbit round a planet by Kepler, the rest through the
    #: integrator as one batch (D-354) -- the same split `state_at` makes.
    free: list[tuple[Ship, float]] = []
    held: list[tuple[Ship, sky.Bound]] = []
    for one in coasting:
        start = await sky_days(session, one.sky_at)
        r0, v0 = _state_of(one)
        orbit = sky.bound_to(world, start, r0, v0)
        if orbit is None or not sky.kepler_reads(world, orbit, _window(constants)):
            free.append((one, start))
        else:
            held.append((one, orbit))
    if held:
        rr, vv = sky.bound_states([orbit for _, orbit in held], t)
        for (one, _), r, v in zip(held, rr, vv, strict=True):
            found[one.id] = ((float(r[0]), float(r[1])), (float(v[0]), float(v[1])))
    if free:
        step = float(constants[R.ORBIT_PLAN_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY
        starts = np.array([start for _, start in free])
        r0s = np.array([_state_of(one)[0] for one, _ in free], dtype=float)
        v0s = np.array([_state_of(one)[1] for one, _ in free], dtype=float)
        #: Either way in time, as `state_at` reads one hull.
        rr, vv = sky.advance(world, starts, np.full_like(starts, t), r0s, v0s, dt_max=step)
        for (one, _), r, v in zip(free, rr, vv, strict=True):
            found[one.id] = ((float(r[0]), float(r[1])), (float(v[0]), float(v[1])))
    for one in ships:
        if one.held_ship_id is not None and one.held_ship_id in found:
            found[one.id] = found[one.held_ship_id]
    return found


def _window(constants: Constants) -> float:
    """How long a stamp is read before the tick flies it on, sky days."""
    return float(constants[R.ORBIT_RESTAMP_HOURS]) / HOURS_PER_DAY


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


async def into_orbit(
    session: AsyncSession,
    ship: Ship,
    sphere: Node,
    *,
    r: tuple[float, float],
    v: tuple[float, float],
    now: datetime,
) -> None:
    """The hull has come into orbit round `sphere`'s planet (D-354): no order
    any more, its state the orbit it is on, its rooms under the planet. No
    node is moored to -- there is none above a planet -- and no edge laid:
    in orbit a hull is a body in the sky, and the only way in or out of it
    is a hull come alongside (`meet`). The coast ahead is the caller's to
    count: it knows the sky it has read."""
    _write_state(ship, r, v, at=now)
    ship.course = None
    await hang_over(session, ship, sphere)
    await session.flush()


async def _constants(session: AsyncSession) -> Constants:
    return current()


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


def leaving_of(
    world: sky.System, t: float, r: tuple[float, float], v: tuple[float, float]
) -> sky.Body | None:
    """The world the hull is in orbit round, from its state, or nothing: what
    a departure leaves (D-316, D-341, D-354)."""
    held = sky.bound_to(world, t, r, v)
    return None if held is None else held.body


async def orbit_of(
    session: AsyncSession, constants: Constants, ship: Ship, *, now: datetime
) -> sky.Bound | None:
    """The orbit this hull is on round a planet (D-354), or nothing: a hull
    that coasts -- no order, no leg, not moored, not lost -- on a closed orbit
    that keeps (`sky.bound_to`). "In orbit" is this reading, not a place: the
    console's stage, the descent's leave and the music aboard all ask it. A
    hull on the hold reads the orbit it flies with its reference."""
    if ship.course or ship.docked_node_id is not None or ship.lost_at is not None:
        return None
    found = await state_at(session, constants, ship, now=now)
    if found is None:
        return None
    r, v, t = found
    return sky.bound_to(await system(session, constants), t, r, v)


async def orbiting(
    session: AsyncSession, constants: Constants, ship: Ship, *, now: datetime
) -> sky.Body | None:
    """The planet this hull is in orbit round, or nothing (`orbit_of`)."""
    held = await orbit_of(session, constants, ship, now=now)
    return None if held is None else held.body


async def _afford(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    dv: float,
    *,
    why: str,
) -> tuple[float, int | None]:
    """Whether the tanks pay for a burn of `dv`: refused if not, else the
    hull's mass and its engines' class for the caller to price the rest by.
    The tanks are locked for the check, as any amount is read before it is
    written off."""
    weight = await mass(session, constants, catalog, ship)
    klass = await engine_class(session, constants, ship)
    stacks = await stock.lock_items(
        session, await fuel_stacks(session, constants, catalog, ship), ordered=True
    )
    worth = sum(amount_float(one.amount) * fuel_energy(constants, one.type_key) for one in stacks)
    need = fuel_for_dv(constants, weight, dv, klass)
    if worth + _DV_EPS < need:
        raise NoFuel(key="ship-no-fuel", why=why, need=need, goods="ship_fuel", have=worth)
    return weight, klass


async def circle(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    thrust_ratio: float,
    now: datetime,
) -> tuple[sky.Sample, float]:
    """The order to circle the star (D-289, 2026-09-04): the burn that matches
    the circle round the star through the hull's own place, prograde, priced
    and written onto the row. The helm flies it (`guide._circle`) and the
    tanks pay as it burns; refused only for a burn the tanks cannot pay for
    whole. Whatever order the hull was under is dropped for this one."""
    #: Flown, not read: the order is written from this state (D-354).
    found = await state_at(session, constants, ship, now=now, exact=True)
    if found is None:  # pragma: no cover -- the caller asks for a hull in the sky
        raise NoArc(key="ship-no-arc", hours=0)
    r, v, _ = found
    world = await system(session, constants)
    plan = sky.circle_quote(world, r, v, thrust_ratio * float(constants[R.ORBIT_THRUST_SCALE]))
    if plan.dv <= world.capture_speed:
        #: Already on the circle, within the helm's own word for it: the order
        #: would be done on its first step and the owner told a second time.
        raise InFlight(key="ship-already-circling", ship=ship.name)
    weight, klass = await _afford(session, constants, catalog, ship, plan.dv, why="orbit")
    _write_state(ship, r, v, at=now)
    ship.held_ship_id = None
    due = _stamp(now + timedelta(hours=plan.hours))
    ship.course = {
        "target": sky.STAR.key,
        "planet": None,
        "ship": None,
        "since": _stamp(now),
        "arrive_at": due,
        "due_at": due,
        "hours": round(plan.hours, ROUND_HOURS),
        "dv": round(plan.dv, ROUND_DV),
        "dv_out": round(plan.dv_out, ROUND_DV),
        "dv_in": 0.0,
        "trace": [[round(x, ROUND_TRACE), round(y, ROUND_TRACE)] for x, y in plan.trace],
        "phase": sky.BURN,
        "spent": 0.0,
    }
    ship.forecast = None
    await session.flush()
    return plan, fuel_for_dv(constants, weight, plan.dv, klass)


async def depart(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    target: Node | Ship,
    *,
    plan: sky.Sample,
    thrust_ratio: float,
    now: datetime,
) -> tuple[sky.Sample, float]:
    """Set the order: the chosen point of the slider, written onto the row.
    Returns the plan and the fuel it will cost by its delta-v.

    `plan` is a point of the slider as the sky offers this hull at the order's
    moment (`slider.point`, D-341). A direct arc's plan is the two-body arc
    the slider showed, no more: a shooting refinement under five bodies was
    tried and dropped for it (D-289, wave 2), because the helm re-solves the
    passage from where the hull actually is every tick (`_fly`) whatever line
    was drawn at the order. A flyby's plan is the refined pass (D-341): the
    order carries the periapsis the helm corrects toward.

    Refused here for what is impossible **now** and for nothing else (D-289):
    tanks that do not hold the departure burn. The arrival burn is the
    console's warning, not the engine's refusal -- fuel may be made on the
    way, and a hull short of it drifts rather than being kept at the pier.
    """
    world = await system(session, constants)
    #: The world arrived at, for the circle a hull falls to; a hull met in
    #: the deep has none.
    goal = None if isinstance(target, Ship) else world.body(target.planet.value)
    hours = plan.hours
    #: Flown, not read: the order is written from this state (D-354).
    found = await state_at(session, constants, ship, now=now, exact=True)
    if found is None:  # pragma: no cover -- the slider answered, so the hull is in the sky
        raise NoArc(key="ship-no-arc", hours=round(hours, ROUND_HOURS))
    r, v, t = found

    weight, klass = await _afford(session, constants, catalog, ship, plan.dv_out, why="cross")
    #: The world a flyby's departure leaves (D-341): the one the hull is in
    #: orbit round, or the one whose hold a drifting hull is in -- the helm's departure lasts while
    #: the hull is still in its grip (`sky.steer_pass`).
    home: str | None = None
    if plan.via is not None:
        assert goal is not None
        left = leaving_of(world, t, r, v)
        held = left or sky.holding(world, goal, t, r, spare=plan.via.via)
        home = None if held is None else held.key

    #: Whoever was holding on to this hull was let go of by the caller
    #: (`hold.release_holders`), from the state they shared; the hull's own
    #: state is written here, and the hold it may itself have been on is
    #: over (wave 3).
    _write_state(ship, r, v, at=now)
    ship.held_ship_id = None
    #: The wait for the ejection window, priced into the hour before it is
    #: promised (D-316). The helm holds the burn until the circle turns the
    #: hull the way the arc leaves; counting that here is what keeps the arc
    #: deliverable -- waiting against an hour fixed for an immediate
    #: departure only makes the arc steeper than the engines can fly.
    #: The wait for the ejection window, as the slider counted it (D-316):
    #: the console showed this hour before the button, and the order promises
    #: the same one.
    wait = plan.wait / HOURS_PER_DAY
    ship.course = {
        "target": None if isinstance(target, Ship) else target.key,
        "planet": None if isinstance(target, Ship) else target.planet.value,
        "ship": str(target.id) if isinstance(target, Ship) else None,
        "since": _stamp(now),
        #: The hour the helm aims at, and the hour the console promises: the
        #: plan's burns are instants, the hull's are stretches, and braking
        #: from the arc's speed at this thrust puts the hull on the circle
        #: later than the arc reaches the planet (`sky.brake_days`).
        "arrive_at": _stamp(now + timedelta(days=wait, hours=hours)),
        "due_at": _stamp(
            now
            + timedelta(days=wait, hours=hours)
            + timedelta(
                days=sky.brake_days(
                    world,
                    plan.dv_in,
                    thrust_ratio * float(constants[R.ORBIT_THRUST_SCALE]),
                    #: The circle fallen to is the target world's own (D-324).
                    goal,
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
    if plan.via is not None:
        ship.course.update(flyby.order_of(plan.via, home=home, now=now, wait=wait))
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
    inertia takes it -- in orbit, the lap round its planet (D-354). Nothing
    for a hull not in the sky: on a pad, on a leg, or lost.

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
        "x": round(r[0], ROUND_NEAR),
        "y": round(r[1], ROUND_NEAR),
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
                #: The planet a lap goes round (D-354): the chart draws the
                #: line round where that planet is now.
                "around": stored.get("around"),
            }
        ),
    }


def _keep_forecast(ship: Ship, fate: sky.Fate, *, now: datetime, t: float) -> None:
    """Write the coast ahead onto the row: the verdict, its hour, the line to
    draw, and the moment it was counted from -- so the next tick knows when
    it has aged (`_forecast_stale`)."""
    #: A lap round a planet is drawn at the planet's own scale (D-354).
    digits = ROUND_NEAR if fate.around else ROUND_TRACE
    ship.forecast = {
        "kind": fate.kind,
        "at": _stamp(now + timedelta(days=fate.at - t)),
        "body": fate.body,
        "trace": [[round(x, digits), round(y, digits)] for x, y in fate.trace],
        "since": _stamp(now),
        #: The line's own length, and whether it is a lap read round and
        #: round: what a rendezvous is aimed along (`drifter_of`).
        "until": _stamp(now + timedelta(days=fate.span)),
        "loops": fate.loops,
        #: The planet a lap goes round, its `trace` drawn round that planet's
        #: centre (D-354): the reader puts the planet under it.
        "around": fate.around,
    }


def _forecast_stale(constants: Constants, ship: Ship, now: datetime) -> bool:
    """Whether the forecast on the row is older than the coaster's cadence."""
    stored = ship.forecast or None
    if stored is None:
        return True
    since = datetime.fromisoformat(str(stored.get("since")))
    return now - since >= timedelta(hours=float(constants[R.ORBIT_RESTAMP_HOURS]))
