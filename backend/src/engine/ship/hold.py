# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The hold (D-289, wave 3): a hull come to rest beside another.

From the moment the helm captures, the two fly as one: the holder's row
keeps a link (`held_ship_id`), its place is read off the reference's row,
and only the reference is ticked. The hold ends with an order to either --
the holders are let go from the shared state, drifters like any other -- and
the tick sweeps what a release could not take: a holder whose row was under
somebody's hand that second, or whose reference has since been lost.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import numpy as np
from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src import sky
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events
from src.engine.ship.belonging import crew_of
from src.engine.ship.fate import _adrift, _lose, book_loss, fate_of_row
from src.engine.ship.physics import mass, sky_days
from src.engine.ship.sim import (
    _DV_EPS,
    _keep_forecast,
    _row,
    _state_of,
    _write_state,
    meetable,
    part_hulls,
    state_at,
    system,
    unmeetable,
)
from src.models.event import EventKind
from src.models.ship import Ship
from src.units import HOURS_PER_DAY, MINUTES_PER_HOUR


async def begin(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    other: Ship,
    r: tuple[float, float],
    v: tuple[float, float],
    *,
    now: datetime,
) -> None:
    """Come to rest beside another hull: from here the two fly as one, and
    this hull's place is read off the other's row. The caller holds the
    other's row (`helm._fly`), which is written here.

    Stamped at the reference's place rather than its own: the two are at
    one point from here on (a gangway may open between them), and a coast
    from this stamp -- when the hold is swept rather than released -- is
    the pair's line, not one half a unit off it.

    **Moving as the momentum says** (D-354): the pair's speed is the
    average of the two by mass -- the reference counting whatever already
    holds on to it -- not the reference's own. The hold takes a hull within
    the hold's speed of the other, and that difference used to be taken off
    for nothing: a free burn of up to `orbit.dock_speed`, and round a planet
    a twentieth of the orbit's speed. The chaser brakes it off with its own
    engines first (`helm._fly`, the last burn of a meeting), so what reaches
    here is only what its tanks could not pay -- and only that moves the
    other hull, whose coast is then counted afresh and its loss booked if it
    now comes down.
    """
    #: Flown, not read: the pair's stamp is written from it (D-354).
    found = await state_at(session, constants, other, now=now, exact=True)
    here, theirs = (r, v) if found is None else (found[0], found[1])
    mine = await mass(session, constants, catalog, ship)
    stack = await mass(session, constants, catalog, other)
    for holder in (
        await session.execute(select(Ship).where(Ship.held_ship_id == other.id))
    ).scalars():
        stack += await mass(session, constants, catalog, holder)
    whole = mine + stack
    speed = (
        theirs
        if whole <= 0.0
        else (
            (mine * v[0] + stack * theirs[0]) / whole,
            (mine * v[1] + stack * theirs[1]) / whole,
        )
    )
    _write_state(other, here, speed, at=now)
    world = await system(session, constants)
    t = await sky_days(session, now)
    verdict = await book_loss(session, constants, other, world, now=now, t=t, r=here, v=speed)
    _keep_forecast(other, verdict, now=now, t=t)
    if float(np.hypot(speed[0] - theirs[0], speed[1] - theirs[1])) > _DV_EPS:
        #: Whoever already holds on to the other flies at the new speed too:
        #: its own stamp is what the sweep lets it go from (`sweep`), and a
        #: stamp off the pair's line released it a speed apart. A row under a
        #: hand this second is left -- its own order ends its hold.
        for holder in (
            (
                await session.execute(
                    select(Ship)
                    .where(Ship.held_ship_id == other.id, Ship.id != ship.id)
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        ):
            _write_state(holder, here, speed, at=now)
    _write_state(ship, here, speed, at=now)
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
    #: Each holder's row locked; one under somebody's hand this second is
    #: skipped -- its own order ends its hold itself, and anything else
    #: leaves it to the sweep next minute.
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
        found = await state_at(session, constants, holder, now=now, exact=True)
        holder.held_ship_id = None
        if found is None:  # pragma: no cover -- a hold is a state
            continue
        r, v, t = found
        _write_state(holder, r, v, at=now)
        await _adrift(session, constants, holder, world, now=now, t=t, r=r, v=v, why="released")
    if holders:
        await session.flush()


def orphaned_holds() -> Select[tuple[uuid.UUID]]:
    """The holders whose reference is not there to be held -- gone, or not
    `meetable` any more -- as the tick asks it, without a lock."""
    reference = aliased(Ship)
    return (
        select(Ship.id)
        .outerjoin(reference, reference.id == Ship.held_ship_id)
        .where(
            Ship.held_ship_id.isnot(None),
            Ship.lost_at.is_(None),
            or_(reference.id.is_(None), unmeetable(reference)),
        )
        .order_by(Ship.id)
    )


def half_docks() -> Select[tuple[uuid.UUID]]:
    """Docking marks with no mirror on the other row."""
    mirror = aliased(Ship)
    return (
        select(Ship.id)
        .outerjoin(mirror, mirror.id == Ship.docked_ship_id)
        .where(Ship.docked_ship_id.isnot(None), mirror.docked_ship_id.is_distinct_from(Ship.id))
        .order_by(Ship.id)
    )


async def sweep(session: AsyncSession, constants: Constants, *, now: datetime) -> int:
    """Holds whose reference is no longer there to be held, and dockings
    marked on one side only -- what the tick comes back for, every minute.

    `release_holders` and `_lose` skip a holder whose row somebody else has
    locked that second, and nothing else ever came back for it: it hung on
    a reference flying somebody's order, moored, held itself, or lost. The
    orphans are found in SQL without a lock, and only they are taken -- a
    lock on every held hull each minute would queue every `dock` and `fly`
    of theirs behind the tick. A holder of a lost reference is lost with it,
    by the verdict on that row; any other is let go from its own stamp
    coasted to now, a drifter like any other. Returns how many were let go.
    """
    orphaned = (await session.execute(orphaned_holds())).scalars().all()
    let_go = 0
    world: sky.System | None = None
    for holder_id in orphaned:
        holder = await session.get(
            Ship, holder_id, with_for_update={"skip_locked": True}, populate_existing=True
        )
        if holder is None or holder.held_ship_id is None or holder.lost_at is not None:
            continue
        held = await session.get(Ship, holder.held_ship_id)
        if held is not None and meetable(held):
            continue
        holder.held_ship_id = None
        if held is not None and holder.docked_ship_id == held.id:
            await part_hulls(session, constants, holder, held)
        holder.docked_ship_id = None
        holder.dock_ask_ship_id = None
        if held is not None and held.lost_at is not None:
            #: The reference came down or left with this hull at its side:
            #: this hull went the same way, and is told so, not "released".
            await _lose(session, constants, holder, fate_of_row(held), now=now)
            continue
        if world is None:
            world = await system(session, constants)
        #: From its own stamp -- the pair's state as of the hold (`begin`)
        #: -- coasted to now: what it did while the reference flew off is
        #: exactly nothing.
        r0, v0 = _state_of(holder)
        t0 = await sky_days(session, holder.sky_at)
        t1 = max(t0, await sky_days(session, now))
        step = float(constants[R.ORBIT_PLAN_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY
        rr, vv = sky.advance(
            world, np.array([t0]), np.array([t1]), np.array([r0]), np.array([v0]), dt_max=step
        )
        r, v = _row(rr), _row(vv)
        _write_state(holder, r, v, at=now)
        await _adrift(session, constants, holder, world, now=now, t=t1, r=r, v=v, why="released")
        let_go += 1
    #: A docking marked on one side only: the other side parted while this
    #: row was locked, or is gone. The edge went with the parting; the mark
    #: goes here. The console never showed it -- `sighting.ties` reads a
    #: docking off both rows -- so nothing is said.
    halves = (await session.execute(half_docks())).scalars().all()
    for half_id in halves:
        one = await session.get(
            Ship, half_id, with_for_update={"skip_locked": True}, populate_existing=True
        )
        if one is None or one.docked_ship_id is None:
            continue
        other = await session.get(Ship, one.docked_ship_id)
        if other is None or other.docked_ship_id != one.id:
            if other is not None:
                await part_hulls(session, constants, one, other)
            one.docked_ship_id = None
    if orphaned or halves:
        await session.flush()
    return let_go
