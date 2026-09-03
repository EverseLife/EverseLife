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

import math
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import sky
from src.constants import Catalog, Constants, current
from src.constants import registry as R
from src.db.base import remember
from src.engine import events, stock, travel
from src.engine.jobs import enqueue, handler
from src.engine.ship import course
from src.engine.ship._base import (
    NoArc,
    NoFuel,
    NotEnoughThrust,
    _gangway_seconds,
    is_orbit,
    orbit_node_of,
)
from src.engine.ship.belonging import crew_of
from src.engine.ship.building import moor_to
from src.engine.ship.physics import (
    efficiency,
    engine_class,
    fuel_energy,
    fuel_stacks,
    mass,
    orbits_of,
    ratio,
    sky_days,
    spend_fuel,
)
from src.models.event import EventKind
from src.models.job import Job, JobKind
from src.models.ship import Ship
from src.models.world import Node, Planet, Surface
from src.units import (
    HOURS_PER_DAY,
    KG_PER_TON,
    MINUTES_PER_HOUR,
    ROUND_DV,
    ROUND_HOURS,
    ROUND_MASS,
    ROUND_TRACE,
    SECONDS_PER_HOUR,
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
    target: sky.Body,
    *,
    now: datetime,
) -> list[sky.Sample]:
    """The slider from where the hull is: the preview for every point of the
    grid (D-271, D-289). Empty for a hull not in the sky."""
    found = await state_at(session, constants, ship, now=now)
    if found is None:
        return []
    r, v, t = found
    world = await system(session, constants)
    leaving = None
    if ship.docked_node_id is not None:
        moored = await session.get(Node, ship.docked_node_id)
        if moored is not None and is_orbit(moored):
            leaving = world.body(moored.planet.value)
    return sky.preview(world, constants, r, v, t, target, course.grid(constants), leaving=leaving)


async def depart(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    target: Node,
    *,
    hours: float,
    thrust_ratio: float,
    now: datetime,
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
    goal = world.body(target.planet.value)
    samples = {
        one.hours: one for one in await offers(session, constants, catalog, ship, goal, now=now)
    }
    sample = samples.get(round(hours, ROUND_HOURS)) or samples.get(hours)
    if sample is None:
        raise NoArc(key="ship-no-arc", hours=round(hours, ROUND_HOURS))
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

    _write_state(ship, r, v, at=now)
    ship.park_phase = None
    ship.course = {
        "target": target.key,
        "planet": target.planet.value,
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


# --- the tick ---------------------------------------------------------------------


async def tick_sky(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    *,
    now: datetime | None = None,
) -> dict[str, float | int]:
    """Move every hull in space up to `now`: fly the ordered, restamp the
    coasting. Returns what happened, for the tick's telemetry."""
    moment = now or datetime.now(UTC)
    afloat = (
        (
            await session.execute(
                select(Ship)
                .where(
                    Ship.docked_node_id.is_(None),
                    Ship.sky_at.isnot(None),
                    Ship.lost_at.is_(None),
                )
                .order_by(Ship.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    if not afloat:
        return {"flown": 0, "moored": 0, "adrift": 0, "fuel": 0.0}
    world = await system(session, constants)
    flown = moored = adrift = 0
    fuel = 0.0
    stale = timedelta(hours=float(constants[R.ORBIT_RESTAMP_HOURS]))
    for ship in afloat:
        if ship.course:
            done, burnt = await _fly(session, constants, catalog, world, ship, now=moment)
            flown += 1
            fuel += burnt
            moored += done == "moored"
            adrift += done == "adrift"
        elif moment - ship.sky_at >= stale:
            await _restamp(session, constants, world, ship, now=moment)
    await session.flush()
    return {"flown": flown, "moored": moored, "adrift": adrift, "fuel": round(fuel, ROUND_MASS)}


async def _fly(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    world: sky.System,
    ship: Ship,
    *,
    now: datetime,
) -> tuple[str, float]:
    """One hull's stretch under its order: step by step, the helm decides, the
    engines burn what the tanks can pay, the sky pulls. Ends moored, adrift,
    or still under way."""
    t0 = await sky_days(session, ship.sky_at)
    t1 = await sky_days(session, now)
    if t1 <= t0:
        return "flying", 0.0
    order = dict(ship.course or {})
    target = world.body(str(order["planet"]))
    arrive = await sky_days(session, datetime.fromisoformat(str(order["arrive_at"])))

    weight = await mass(session, constants, catalog, ship)
    klass = await engine_class(session, constants, ship)
    a_max = (await ratio(session, constants, catalog, ship)) * float(
        constants[R.ORBIT_THRUST_SCALE]
    )
    #: The tanks, locked once for the stretch: what they hold is the budget
    #: of speed, and what is actually burnt is written off at the end under
    #: the same lock (the quality bar: amounts change under the row lock).
    stacks = await stock.lock_items(
        session, await fuel_stacks(session, constants, catalog, ship), ordered=True
    )
    worth = sum(amount_float(one.amount) * fuel_energy(constants, one.type_key) for one in stacks)
    budget = dv_aboard(constants, worth, weight, klass)

    r, v = _state_of(ship)
    step = float(constants[R.ORBIT_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY
    t = t0
    spent = 0.0
    phase = str(order.get("phase", sky.BURN))
    outcome = "flying"
    while t < t1 - sky.TIME_EPS:
        dt = min(step, t1 - t)
        helm = sky.steer(world, target, t, r, v, arrive=arrive, a_max=a_max, dt=dt)
        if helm.captured:
            outcome = "moored"
            break
        thrust = np.array(helm.thrust)
        wanted = float(np.hypot(*thrust)) * dt
        if wanted > _DV_EPS and wanted > budget - spent:
            #: The tanks run out mid-step: burn what is left, then the coast.
            share = max(0.0, budget - spent) / wanted
            thrust = thrust * share
            wanted *= share
            outcome = "adrift"
        rr, vv = sky.advance(
            world,
            np.array([t]),
            np.array([t + dt]),
            np.array([r]),
            np.array([v]),
            dt_max=dt,
            thrust=thrust[None, :],
        )
        r, v = _row(rr), _row(vv)
        spent += wanted
        phase = helm.phase
        t += dt
        if outcome == "adrift":
            break
    if outcome == "adrift" and t < t1 - sky.TIME_EPS:
        rr, vv = sky.advance(
            world,
            np.array([t]),
            np.array([t1]),
            np.array([r]),
            np.array([v]),
            dt_max=float(constants[R.ORBIT_PLAN_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY,
        )
        r, v = _row(rr), _row(vv)
        t = t1

    burnt = 0.0
    if spent > _DV_EPS:
        burnt = await spend_fuel(
            session,
            constants,
            catalog,
            ship,
            fuel_for_dv(constants, weight, spent, klass),
            stacks=stacks,
        )
    stamp = now if outcome != "moored" else _moment_of(now, t1, t)
    _write_state(ship, r, v, at=stamp)

    if outcome == "moored":
        orbit = await orbit_node_of(session, target_planet(target))
        if orbit is None:  # pragma: no cover -- the seed lays one per planet
            outcome = "flying"
        else:
            p, _ = sky.place(target, t)
            rel = np.array(r) - p[0]
            await moor(session, ship, orbit, now=stamp, phase=float(math.atan2(rel[1], rel[0])))
            await events.record(
                session,
                EventKind.SHIP_DOCKED,
                actor_identity_id=ship.owner_identity_id,
                node_id=orbit.id,
                ship_id=str(ship.id),
                name=ship.name,
                port=orbit.key,
            )
            return outcome, burnt
    if outcome == "adrift":
        ship.course = None
        await _adrift(session, constants, ship, world, now=now, t=t1, r=r, v=v)
        return outcome, burnt
    order["phase"] = phase
    order["spent"] = round(float(order.get("spent", 0.0)) + spent, ROUND_DV)
    ship.course = order
    #: "If the engines fell silent now": the coast ahead of a hull under an
    #: order, refreshed at the coaster's cadence rather than every minute --
    #: ninety days of five-body arithmetic is the tick's to spend, not a
    #: reading's, and not every tick's either.
    if _forecast_stale(constants, ship, now):
        _keep_forecast(ship, await fate_of(session, constants, world, t1, r, v), now=now, t=t1)
    await session.flush()
    return outcome, burnt


def target_planet(body: sky.Body) -> Planet:
    return Planet(body.key)


def _moment_of(now: datetime, t1: float, t: float) -> datetime:
    """The clock moment of sky day `t`, counted back from `now` at `t1`."""
    return now - timedelta(days=t1 - t)


async def _adrift(
    session: AsyncSession,
    constants: Constants,
    ship: Ship,
    world: sky.System,
    *,
    now: datetime,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
) -> None:
    """The engines ran dry: say so to everybody aboard, and book the hour the
    coast ends, if it ends."""
    crew = await crew_of(session, ship)
    aboard = {
        f"crew{seat}_identity_id": str(member.identity_id) for seat, member in enumerate(crew)
    }
    await events.record(
        session,
        EventKind.SHIP_ADRIFT,
        actor_identity_id=ship.owner_identity_id,
        node_id=ship.connector_node_id,
        ship_id=str(ship.id),
        name=ship.name,
        crew=len(crew),
        **aboard,
    )
    fate = await book_loss(session, constants, ship, world, now=now, t=t, r=r, v=v)
    _keep_forecast(ship, fate, now=now, t=t)


async def _restamp(
    session: AsyncSession, constants: Constants, world: sky.System, ship: Ship, *, now: datetime
) -> None:
    """Move a coasting hull's stamp along, so a reading never propagates weeks."""
    found = await state_at(session, constants, ship, now=now)
    if found is None:  # pragma: no cover -- the tick selected a hull in the sky
        return
    r, v, t = found
    _write_state(ship, r, v, at=now)
    #: And the coast ahead, from the new stamp: what the console and the map
    #: read as the drifter's line and verdict.
    _keep_forecast(ship, await fate_of(session, constants, world, t, r, v), now=now, t=t)


# --- the end of a coast --------------------------------------------------------


async def fate_of(
    session: AsyncSession, constants: Constants, world: sky.System, t: float, r, v
) -> sky.Fate:
    horizon = float(constants[R.ORBIT_FORECAST_DAYS])
    step = float(constants[R.ORBIT_PLAN_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY
    return sky.inertia(world, t, r, v, horizon=horizon, dt_max=step)


async def book_loss(
    session: AsyncSession,
    constants: Constants,
    ship: Ship,
    world: sky.System,
    *,
    now: datetime,
    t: float,
    r: tuple[float, float],
    v: tuple[float, float],
) -> sky.Fate:
    """The forecast's hour as a job: nothing polls a coast, the journal wakes
    at the hour and asks the arithmetic once more."""
    fate = await fate_of(session, constants, world, t, r, v)
    if fate.kind != sky.STABLE:
        at = now + timedelta(days=fate.at - t)
        await enqueue(
            session,
            JobKind.SHIP_LOSS,
            at,
            payload={"ship": str(ship.id), "kind": fate.kind, "body": fate.body},
            dedup_key=f"ship.loss:{ship.id}:{at.isoformat()}",
        )
    return fate


@handler(JobKind.SHIP_LOSS)
async def lost(session: AsyncSession, job: Job) -> None:
    """The hour has come: is the hull where the forecast said? A hull that
    burned, was refuelled and ordered on, or was moored since, is left alone;
    one still coasting is asked the arithmetic again and lost only if it
    really came down or really left."""
    constants = await _constants(session)
    ship = await session.get(Ship, uuid.UUID(str(job.payload["ship"])), with_for_update=True)
    if ship is None or ship.lost_at is not None or ship.docked_node_id is not None:
        return
    if ship.course or ship.sky_at is None:
        return
    world = await system(session, constants)
    found = await state_at(session, constants, ship, now=job.run_at)
    if found is None:  # pragma: no cover -- the checks above are the same question
        return
    r, v, t = found
    fate = await fate_of(session, constants, world, t, r, v)
    #: Down or gone within a step of now: the forecast was right.
    grace = float(constants[R.ORBIT_PLAN_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY
    if fate.kind != sky.STABLE and fate.at <= t + grace:
        await _lose(session, constants, ship, fate, now=job.run_at)
        return
    #: Not yet -- the coast is slower than the forecast thought, or a nudge
    #: since moved the hour. Booked again at the new hour, if there is one.
    _write_state(ship, r, v, at=job.run_at)
    fate = await book_loss(session, constants, ship, world, now=job.run_at, t=t, r=r, v=v)
    _keep_forecast(ship, fate, now=job.run_at, t=t)


async def _lose(
    session: AsyncSession, constants: Constants, ship: Ship, fate: sky.Fate, *, now: datetime
) -> None:
    from src.engine import death  # noqa: PLC0415 -- lazy: breaks the cycle with death

    crew = await crew_of(session, ship)
    for member in crew:
        await death.die(
            session, constants, member, cause=CRASHED if fate.kind == sky.CRASH else LOST, now=now
        )
    ship.lost_at = now
    ship.course = None
    await session.flush()
    aboard = {
        f"crew{seat}_identity_id": str(member.identity_id) for seat, member in enumerate(crew)
    }
    await events.record(
        session,
        EventKind.SHIP_LOST,
        actor_identity_id=ship.owner_identity_id,
        node_id=ship.connector_node_id,
        ship_id=str(ship.id),
        name=ship.name,
        fate=fate.kind,
        body=fate.body or "",
        crew=len(crew),
        **aboard,
    )


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
    stored = ship.forecast or None
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
    }


def _forecast_stale(constants: Constants, ship: Ship, now: datetime) -> bool:
    """Whether the forecast on the row is older than the coaster's cadence."""
    stored = ship.forecast or None
    if stored is None:
        return True
    since = datetime.fromisoformat(str(stored.get("since")))
    return now - since >= timedelta(hours=float(constants[R.ORBIT_RESTAMP_HOURS]))


def seconds_of(hours: float) -> float:
    return hours * SECONDS_PER_HOUR
