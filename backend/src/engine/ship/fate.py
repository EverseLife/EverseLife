# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Where a coast ends (D-289): the forecast booked as a job, and the loss.

The floor under the helm's tick and the hold's sweep. A hull that goes
adrift is told, and the hour its coast ends is booked as a job rather than
polled for; at that hour the journal asks the arithmetic once more and kills
only what really came down or really left -- and, with it, whoever flies as
one with it (wave 3).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import sky
from src.constants import Constants
from src.constants import registry as R
from src.engine import events
from src.engine.jobs import enqueue, handler
from src.engine.ship.belonging import crew_of
from src.engine.ship.sim import (
    CRASHED,
    LOST,
    _constants,
    _keep_forecast,
    _write_state,
    part_hulls,
    state_at,
    system,
)
from src.models.event import EventKind
from src.models.job import Job, JobKind
from src.models.ship import Ship
from src.units import HOURS_PER_DAY, MINUTES_PER_HOUR


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
    """The engines ran dry -- or the target went, or the hold was let go of
    (wave 3): say so to everybody aboard, and book the hour the coast ends,
    if it ends."""
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


async def fate_of(
    session: AsyncSession, constants: Constants, world: sky.System, t: float, r, v
) -> sky.Fate:
    horizon = float(constants[R.ORBIT_FORECAST_DAYS])
    step = float(constants[R.ORBIT_PLAN_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY
    #: Off the loop: a coast that really has to be flown is seconds of
    #: numpy, and the worker's other steps must not wait on it.
    return await asyncio.to_thread(sky.inertia, world, t, r, v, horizon=horizon, dt_max=step)


def fate_of_row(ship: Ship) -> sky.Fate:
    """The verdict written on a lost hull's row (`_lose`), for a companion
    lost after the fact (`hold.sweep`): the loss reads the kind and the
    body, and the line is not flown again. A row without one came down on
    what its last word named, or on nothing -- gone."""
    stored = ship.forecast or {}
    body = stored.get("body")
    kind = str(stored.get("kind") or sky.STABLE)
    if kind == sky.STABLE:
        kind = sky.CRASH if body else sky.ESCAPE
    return sky.Fate(
        kind=kind,
        at=0.0,
        body=None if body is None else str(body),
        trace=(),
        span=0.0,
        loops=False,
    )


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
    #: The line ends here: the verdict written on the row, for whoever is
    #: lost after it by this row's word (`hold.sweep`, `fate_of_row`).
    _keep_forecast(ship, fate, now=now, t=fate.at)
    await session.flush()
    aboard = {
        f"crew{seat}_identity_id": str(member.identity_id) for seat, member in enumerate(crew)
    }
    #: Whoever flies as one with this hull goes with it (wave 3): the hull on
    #: its hold, and the hull docked to it -- they are at the same place.
    #: Locked, and skipped when locked by an order of their own: a companion
    #: that left a second ago is not at this place any more. One skipped for
    #: any other reason is still on the hold, and the sweep loses it by the
    #: verdict left on this row (`hold.sweep`, `fate_of_row`).
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
        #: The gangway goes with the mark, whether or not the other row could
        #: be taken: the edge is the roads', not the row's.
        joined = partner if partner is not None else await session.get(Ship, ship.docked_ship_id)
        if joined is not None:
            await part_hulls(session, constants, ship, joined)
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
