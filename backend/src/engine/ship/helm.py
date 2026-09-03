# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The helm at the tick (D-289): every hull in the sky moved up to now.

The floor above `sim`, which owns the state: here the ordered hulls are
flown step by step -- the helm decides, the engines burn what the tanks can
pay, the sky pulls -- and come to their ends: moored on a planet's circle,
come to rest beside another hull (wave 3), or adrift with the tanks dry. A
coasting hull has its stamp moved along and its coast ahead counted; the
hour a coast ends is a job that asks the arithmetic again before it kills.
Who came into sight while a hull moved is told here too.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import sky
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, stock
from src.engine.jobs import enqueue, handler
from src.engine.ship._base import orbit_node_of
from src.engine.ship.belonging import crew_of
from src.engine.ship.physics import (
    engine_class,
    fuel_energy,
    fuel_stacks,
    mass,
    ratio,
    sky_days,
    spend_fuel,
)
from src.engine.ship.sim import (
    _DV_EPS,
    CRASHED,
    LOST,
    _constants,
    _forecast_stale,
    _keep_forecast,
    _row,
    _state_of,
    _write_state,
    dv_aboard,
    fuel_for_dv,
    meetable,
    moor,
    state_at,
    states_at,
    system,
)
from src.models.event import EventKind
from src.models.job import Job, JobKind
from src.models.ship import Ship
from src.models.world import Planet
from src.units import HOURS_PER_DAY, MINUTES_PER_HOUR, ROUND_DV, ROUND_MASS, amount_float


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
    #: Which hulls have work this tick, read without a lock; then each one
    #: is taken for update by itself, and one somebody is ordering right now
    #: is skipped until the next minute. A single lock over every hull in
    #: the sky held all of `ship.fly` and `ship.recall` behind the slowest
    #: forecast (review of this wave).
    stale = timedelta(hours=float(constants[R.ORBIT_RESTAMP_HOURS]))
    wanted = (
        (
            await session.execute(
                select(Ship.id)
                .where(
                    Ship.docked_node_id.is_(None),
                    Ship.sky_at.isnot(None),
                    Ship.lost_at.is_(None),
                    Ship.held_ship_id.is_(None),
                    (Ship.course.isnot(None)) | (Ship.sky_at <= moment - stale),
                )
                .order_by(Ship.id)
            )
        )
        .scalars()
        .all()
    )
    if not wanted:
        return {"flown": 0, "moored": 0, "adrift": 0, "held": 0, "fuel": 0.0}
    world = await system(session, constants)
    flown = moored = adrift = held = 0
    fuel = 0.0
    moved: list[Ship] = []
    for ship_id in wanted:
        ship = await session.get(
            Ship, ship_id, with_for_update={"skip_locked": True}, populate_existing=True
        )
        if ship is None or ship.docked_node_id is not None or ship.lost_at is not None:
            continue
        if ship.course:
            done, burnt = await _fly(session, constants, catalog, world, ship, now=moment)
            flown += 1
            fuel += burnt
            moored += done == "moored"
            adrift += done == "adrift"
            held += done == "held"
        elif moment - ship.sky_at >= stale:
            await _restamp(session, constants, world, ship, now=moment)
        else:
            continue
        moved.append(ship)
    #: And who came into sight while they moved (wave 3): every hull in the
    #: sky placed once, the pairs read off that one table.
    if moved:
        afloat = (
            (
                await session.execute(
                    select(Ship).where(Ship.sky_at.isnot(None), Ship.lost_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        table = await states_at(session, constants, afloat, now=moment)
        for ship in moved:
            await _sight(session, world, ship, afloat, table)
    await session.flush()
    return {
        "flown": flown,
        "moored": moored,
        "adrift": adrift,
        "held": held,
        "fuel": round(fuel, ROUND_MASS),
    }


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
    arrive = await sky_days(session, datetime.fromisoformat(str(order["arrive_at"])))
    #: The target: a planet on its circle, or a hull on its forecast (wave 3).
    #: A hull that is no longer there to be met -- lost, moored since, under
    #: an order of its own, or on somebody's hold -- voids the order: the
    #: chaser coasts from where it is, and its owner is told as of a drift.
    other: Ship | None = None
    target: sky.Target
    if order.get("ship"):
        other_id = uuid.UUID(str(order["ship"]))
        if await session.get(Ship, other_id) is None:
            return await _void(session, constants, world, ship, now=now, t0=t0, t1=t1)
        #: The target's row, locked for the stretch: an order given to it in
        #: the same second must not slip in between the reading and the
        #: hold. Locked by somebody else right now -- the next minute will do.
        other = await session.get(
            Ship, other_id, with_for_update={"skip_locked": True}, populate_existing=True
        )
        if other is None:
            return "flying", 0.0
        if not meetable(other) or other.id == ship.id:
            return await _void(session, constants, world, ship, now=now, t0=t0, t1=t1)
        found_goal = await _dense_drifter(session, constants, world, other, t0=t0, t1=t1)
        if found_goal is None:
            return "flying", 0.0
        target = found_goal
    else:
        target = world.body(str(order["planet"]))

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

    if outcome == "moored" and other is not None:
        await _hold(session, constants, ship, other, r, v, now=stamp)
        return "held", burnt
    if outcome == "moored" and isinstance(target, sky.Body):
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


async def _dense_drifter(
    session: AsyncSession,
    constants: Constants,
    world: sky.System,
    other: Ship,
    *,
    t0: float,
    t1: float,
) -> sky.Drifter | None:
    """The target hull's line for the stretch being flown, laid densely.

    The forecast on its row is the chart's line -- a couple of dozen points
    over months, coarse enough to miss a hull by units between two of them
    -- so the helm is given the same coast propagated afresh **from the
    hull's own stamp**, a point an hour, to a while past the stretch's end
    (`_meet` wants the hull's speed as well as its place). From the stamp
    and not from the stretch's start: the target may have been restamped
    this very tick, and a state asked for before a stamp is the stamp's --
    an hour's shift that read as the target jumping a unit.
    """
    if other.sky_at is None or other.held_ship_id is not None:
        return None
    r, v = _state_of(other)
    start = await sky_days(session, other.sky_at)
    step = float(constants[R.ORBIT_PLAN_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY
    horizon = max(t1 - start, step) + max(t1 - t0, step) + step
    points = int(math.ceil(horizon * HOURS_PER_DAY)) + 1 + 1
    path = sky.sample(
        world,
        start,
        np.array([r]),
        np.array([v]),
        np.array([horizon]),
        dt_max=step,
        points=points,
    )[0]
    return sky.Drifter(
        key=f"ship:{other.id}:{start}",
        t0=start,
        t1=start + horizon,
        trace=tuple((float(x), float(y)) for x, y in path),
    )


async def _void(
    session: AsyncSession,
    constants: Constants,
    world: sky.System,
    ship: Ship,
    *,
    now: datetime,
    t0: float,
    t1: float,
) -> tuple[str, float]:
    """The order's target is gone: the hull coasts on from where it is."""
    r0, v0 = _state_of(ship)
    step = float(constants[R.ORBIT_PLAN_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY
    rr, vv = sky.advance(
        world, np.array([t0]), np.array([t1]), np.array([r0]), np.array([v0]), dt_max=step
    )
    r, v = _row(rr), _row(vv)
    _write_state(ship, r, v, at=now)
    ship.course = None
    await _adrift(session, constants, ship, world, now=now, t=t1, r=r, v=v, why="target")
    return "adrift", 0.0


async def _hold(
    session: AsyncSession,
    constants: Constants,
    ship: Ship,
    other: Ship,
    r: tuple[float, float],
    v: tuple[float, float],
    *,
    now: datetime,
) -> None:
    """Come to rest beside another hull (D-289, wave 3): from here the two
    fly as one, and this hull's place is read off the other's row."""
    _write_state(ship, r, v, at=now)
    ship.course = None
    ship.forecast = None
    ship.held_ship_id = other.id
    await session.flush()
    crew = await crew_of(session, ship)
    aboard = {
        f"crew{seat}_identity_id": str(member.identity_id) for seat, member in enumerate(crew)
    }
    #: Both owners are told: the one who came, and the one who was come to.
    for teller in {ship.owner_identity_id, other.owner_identity_id}:
        await events.record(
            session,
            EventKind.SHIP_HELD,
            actor_identity_id=teller,
            node_id=ship.connector_node_id,
            ship_id=str(ship.id),
            name=ship.name,
            other_ship_id=str(other.id),
            other=other.name,
            crew=len(crew),
            **aboard,
        )


async def release_holders(
    session: AsyncSession, constants: Constants, world: sky.System, ship: Ship, *, now: datetime
) -> None:
    """Whoever holds on to this hull is let go of: each takes the shared state
    as its own and coasts alone from here -- a drifter like any other, with
    its coast counted, its loss booked and its owner told. Called before this
    hull's own state changes (an order)."""
    #: Each holder's row locked; one giving its own order this second is
    #: skipped -- its `depart` ends its hold itself.
    holders = (
        (
            await session.execute(
                select(Ship)
                .where(Ship.held_ship_id == ship.id)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    for holder in holders:
        found = await state_at(session, constants, holder, now=now)
        holder.held_ship_id = None
        if found is None:  # pragma: no cover -- a hold is a state
            continue
        r, v, t = found
        _write_state(holder, r, v, at=now)
        await _adrift(session, constants, holder, world, now=now, t=t, r=r, v=v, why="released")
    if holders:
        await session.flush()


async def _sight(
    session: AsyncSession,
    world: sky.System,
    ship: Ship,
    afloat: Sequence[Ship],
    table: dict[uuid.UUID, tuple[tuple[float, float], tuple[float, float]]],
) -> None:
    """Who is within the sight radius of a hull that just moved (D-289, wave 3):
    a foreign hull newly in sight is told of to both owners, once, and a hull
    gone out of sight may be sighted again. `table` is every hull's place
    this tick (`sim.states_at`)."""
    mine = table.get(ship.id)
    if mine is None:  # pragma: no cover -- the tick just wrote the state
        return
    seen: list[str] = []
    for other in afloat:
        if other.id == ship.id or other.owner_identity_id == ship.owner_identity_id:
            continue
        theirs = table.get(other.id)
        if theirs is None:
            continue
        if math.hypot(mine[0][0] - theirs[0][0], mine[0][1] - theirs[0][1]) <= world.sight_radius:
            seen.append(str(other.id))
    before = set(ship.sightings or [])
    for other in afloat:
        if other.id == ship.id or other.owner_identity_id == ship.owner_identity_id:
            continue
        #: Kept on both rows, so that when both hulls move in one tick the
        #: second one's pass finds the pair already known and says nothing.
        theirs_before = set(other.sightings or [])
        if str(other.id) in seen and str(other.id) not in before:
            for teller, seer, seen_one in (
                (ship.owner_identity_id, ship, other),
                (other.owner_identity_id, other, ship),
            ):
                await events.record(
                    session,
                    EventKind.SHIP_SIGHTED,
                    actor_identity_id=teller,
                    node_id=seer.connector_node_id,
                    ship_id=str(seer.id),
                    name=seer.name,
                    other_ship_id=str(seen_one.id),
                    other=seen_one.name,
                )
            other.sightings = sorted(theirs_before | {str(ship.id)})
        elif str(other.id) not in seen and str(ship.id) in theirs_before:
            other.sightings = sorted(theirs_before - {str(ship.id)})
    if sorted(seen) != sorted(before):
        ship.sightings = sorted(seen)


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
    why: str = "fuel",
) -> None:
    """The engines ran dry -- or the target went (wave 3): say so to everybody
    aboard, and book the hour the coast ends, if it ends."""
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
        why=why,
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


async def fate_of(
    session: AsyncSession, constants: Constants, world: sky.System, t: float, r, v
) -> sky.Fate:
    horizon = float(constants[R.ORBIT_FORECAST_DAYS])
    step = float(constants[R.ORBIT_PLAN_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY
    #: Off the loop: a coast that really has to be flown is seconds of
    #: numpy, and the worker's other steps must not wait on it.
    return await asyncio.to_thread(sky.inertia, world, t, r, v, horizon=horizon, dt_max=step)


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
    fate: sky.Fate | None = None,
) -> sky.Fate:
    """The forecast's hour as a job: nothing polls a coast, the journal wakes
    at the hour and asks the arithmetic once more. `fate` is passed by a
    caller that has just counted it, so the coast is not flown twice."""
    if fate is None:
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
    await book_loss(session, constants, ship, world, now=job.run_at, t=t, r=r, v=v, fate=fate)
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
    #: Whoever flies as one with this hull goes with it (wave 3): the hull on
    #: its hold, and the hull docked to it -- they are at the same place.
    #: Locked, and skipped when locked by an order of their own: a companion
    #: that left a second ago is not at this place any more.
    companions = list(
        (
            await session.execute(
                select(Ship)
                .where(Ship.held_ship_id == ship.id)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    if ship.docked_ship_id is not None:
        partner = await session.get(
            Ship, ship.docked_ship_id, with_for_update={"skip_locked": True}, populate_existing=True
        )
        if partner is not None:
            companions.append(partner)
    ship.held_ship_id = None
    ship.docked_ship_id = None
    for companion in companions:
        if companion.lost_at is None and companion.id != ship.id and not companion.course:
            companion.held_ship_id = None
            companion.docked_ship_id = None
            await _lose(session, constants, companion, fate, now=now)
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
