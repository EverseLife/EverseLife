# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What a hull sees of the others in the sky (D-289, wave 3), and what it may aim at.

One's own hulls always; foreign ones within the sight radius or moored at
the same planet -- and only what is seen may be the target of an order. The
hold and the docking are read here too, for the console. Reads write
nothing: the journal is told of a sighting by the tick (`helm._sight`), and
the consents are written by `meet`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.db.base import remember
from src.engine.ship import sim
from src.engine.ship._base import ShipError, TooFar
from src.models.ship import Ship
from src.units import ROUND_TRACE

#: What a sighted hull is doing, in the console's words.
MOORED = "orbit"
UNDER_WAY = "flight"
ADRIFT = "adrift"
HELD = "held"


async def sightings(
    session: AsyncSession, constants: Constants, ship: Ship, *, now: datetime
) -> list[dict[str, object]]:
    """Who else is in the sky near this hull: one's own hulls always, foreign
    ones within the sight radius of this hull or moored at the same planet.

    Each with where it is, what it is doing, whose it is, and whether it may
    be aimed at -- a drifter with a forecast, on nobody's hold, is a target
    the chart offers the way it offers a planet.
    """
    world = await sim.system(session, constants)
    afloat, table = await _placed(session, constants, now=now)
    mine = table.get(ship.id)
    seen: list[dict[str, object]] = []
    for other in afloat:
        if other.id == ship.id:
            continue
        theirs = table.get(other.id)
        if theirs is None:
            continue
        own = other.owner_identity_id == ship.owner_identity_id
        near = mine is not None and (
            math.hypot(mine[0][0] - theirs[0][0], mine[0][1] - theirs[0][1]) <= world.sight_radius
        )
        same_orbit = ship.docked_node_id is not None and other.docked_node_id == ship.docked_node_id
        if not (own or near or same_orbit):
            continue
        seen.append(
            {
                "ship": str(other.id),
                "name": other.name,
                "x": round(theirs[0][0], ROUND_TRACE),
                "y": round(theirs[0][1], ROUND_TRACE),
                "doing": _doing(other),
                "mine": own,
                #: What the chart may aim this hull at: a drifter with a line
                #: to be met on, and not one already flying as one with
                #: somebody -- a hold has one reference, and that is it; nor
                #: the hull this one already rests beside.
                "target": (
                    sim.meetable(other) and bool(other.forecast) and other.id != ship.held_ship_id
                ),
            }
        )
    return seen


async def _placed(
    session: AsyncSession, constants: Constants, *, now: datetime
) -> tuple[Sequence[Ship], dict]:
    """Every hull in the sky and where it is, once per command: a fleet's
    console asks for each of its hulls, and the table is the same for all."""

    async def read() -> tuple[Sequence[Ship], dict]:
        afloat = (
            (
                await session.execute(
                    select(Ship).where(Ship.sky_at.isnot(None), Ship.lost_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        return afloat, await sim.states_at(session, constants, afloat, now=now)

    return await remember(session, ("sky.placed", now), read)


def _doing(other: Ship) -> str:
    if other.docked_node_id is not None:
        return MOORED
    if other.course:
        return UNDER_WAY
    if other.held_ship_id is not None:
        return HELD
    return ADRIFT


async def ties(session: AsyncSession, ship: Ship) -> dict[str, object]:
    """The hold and the docking, for the console: the hull this one flies as
    one with (either way round), the hull it is joined to by an edge, and
    where the consents stand."""
    mate = await partner(session, ship)
    return {
        "held": (None if mate is None else {"ship": str(mate.id), "name": mate.name}),
        #: Joined by an edge or not: the edge is to the hull held, so its
        #: name is `held.name` and is not sent twice (D-225).
        "docked_to_ship": ship.docked_ship_id is not None,
        #: Consent given by this hull and not yet returned; consent the other
        #: hull has given and this one has not.
        "dock": {
            "asked": mate is not None and ship.dock_ask_ship_id == mate.id,
            "wanted": mate is not None and mate.dock_ask_ship_id == ship.id,
        },
    }


async def partner(session: AsyncSession, ship: Ship) -> Ship | None:
    """The hull this one flies as one with: the one it holds on to, or the
    one holding on to it."""
    if ship.held_ship_id is not None:
        return await session.get(Ship, ship.held_ship_id)
    return (
        (await session.execute(select(Ship).where(Ship.held_ship_id == ship.id))).scalars().first()
    )


async def aimable(
    session: AsyncSession, constants: Constants, ship: Ship, other: Ship, *, now: datetime
) -> None:
    """Whether `other` may be the target of an order from `ship`; a refusal
    says what stands in the way."""
    if other.id == ship.id:
        raise TooFar(key="ship-target-self")
    if not sim.meetable(other):
        raise TooFar(key="ship-target-not-adrift")
    if not await _in_sight(session, constants, ship, other, now=now):
        world = await sim.system(session, constants)
        raise TooFar(key="ship-target-unseen", radius=world.sight_radius)


async def aimable_quietly(
    session: AsyncSession, constants: Constants, ship: Ship, other: Ship, *, now: datetime
) -> bool:
    try:
        await aimable(session, constants, ship, other, now=now)
    except ShipError:
        return False
    return True


async def _in_sight(
    session: AsyncSession, constants: Constants, ship: Ship, other: Ship, *, now: datetime
) -> bool:
    if other.owner_identity_id == ship.owner_identity_id:
        return True
    _, table = await _placed(session, constants, now=now)
    mine, theirs = table.get(ship.id), table.get(other.id)
    if mine is None or theirs is None:
        return False
    world = await sim.system(session, constants)
    if math.hypot(mine[0][0] - theirs[0][0], mine[0][1] - theirs[0][1]) <= world.sight_radius:
        return True
    return ship.docked_node_id is not None and other.docked_node_id == ship.docked_node_id


def paired(ship: Ship, other: Ship) -> bool:
    """Whether the two fly as one, either way round."""
    return ship.held_ship_id == other.id or other.held_ship_id == ship.id
