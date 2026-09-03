# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The breathing itself: the step out that demands air, the body's hours
settled from what it carries, the hull's hours breathed off the life
support's line -- and the deaths when either runs dry.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, stock
from src.engine import ship as vessels
from src.engine.oxygen._base import (
    _EPS,
    ASPHYXIA,
    SUIT,
    Breath,
    NoAir,
    airless_planets,
    free_air,
    sealed,
)
from src.engine.oxygen.supply import (
    breathable_stacks,
    carried,
    cylinders,
    hull_draw,
    reserve,
    suited,
)
from src.engine.ship import lines
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.ship import Ship
from src.models.world import Node
from src.units import (
    ROUND_AMOUNT,
    ROUND_REMAINDER,
    SECONDS_PER_HOUR,
    amount,
    amount_float,
    on_grid,
)

# --- the step out --------------------------------------------------------------


async def require_air(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    target: Node,
    *,
    seconds: float = 0.0,
) -> None:
    """Refuse a step into a place there is nothing to breathe with (D-233).

    Asked **before** the walk, never at the far end: death by ignorance in one
    click is not this world's way, and a body that set out is a body that will
    arrive. What is checked is the destination's air, then the hull's tanks if
    the destination is a hull, then what the body itself carries.

    And carried **enough for the road**: a drop in the bottom of a cylinder is
    not a licence for a six-hour crossing of the black fields, and letting one
    be would be the very death the refusal exists to prevent -- one click later
    than the click, but no more foreseen.
    """
    if body.state is not BodyState.ALIVE:  # pragma: no cover -- the dead do not walk
        return
    if await free_air(session, target):
        return
    ship = await vessels.of_node(session, target)
    if ship is not None and await reserve(session, constants, catalog, ship) > _EPS:
        return
    if not await suited(session, catalog, body):
        raise NoAir(key="oxygen-no-suit", node=target.name, suit=SUIT)
    have = await carried(session, body)
    need = seconds / SECONDS_PER_HOUR * constants[R.OXYGEN_BODY_DRAW]
    if have <= _EPS:
        raise NoAir(key="oxygen-tanks-empty", node=target.name)
    if have + _EPS < need:
        raise NoAir(key="oxygen-not-enough", node=target.name, need=need, have=have)


# --- the body's own breathing --------------------------------------------------


async def _lock(session: AsyncSession, body: Body) -> Body:
    """The body's row, locked for this transaction -- the same lock the cold takes."""
    return (
        (
            await session.execute(
                select(Body)
                .where(Body.id == body.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one()
    )


async def settle(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    *,
    now: datetime | None = None,
) -> Breath:
    """Bring a body's breathing up to "now".

    Charges **only a body outside**: a body aboard breathes the hull, and the
    hull is settled once for its whole crew (`tick_ships`). The two never
    overlap, and the split is by where the body stands -- there is no third
    place to be.

    The stamp moves in every case all the same, so hours spent in a Terran yard
    are never charged to a cylinder afterwards.
    """
    moment = now or datetime.now(UTC)
    locked = await _lock(session, body)
    node = await session.get(Node, locked.node_id)
    if node is None:  # pragma: no cover -- a body without a node is a bug
        return Breath(left=0.0, uncovered=0.0)

    hours = (moment - locked.air_at).total_seconds() / SECONDS_PER_HOUR
    #: "Up to now" does not work backwards: a tick step carries the nominal
    #: moment of its tick and can arrive behind a command that settled a second
    #: ago. Writing the older stamp back would hand those seconds to the next
    #: settling to charge again -- the same rule the cold keeps.
    if hours <= 0:
        return Breath(left=await carried(session, locked), uncovered=0.0)

    if await free_air(session, node) or vessels.is_aboard(node):
        #: Nothing was owed for the stretch, so it is over and done with.
        locked.air_at = moment
        await session.flush()
        return Breath(left=await carried(session, locked), uncovered=0.0)

    draw = constants[R.OXYGEN_BODY_DRAW]
    need = hours * draw
    if not await suited(session, catalog, locked):
        #: A bare body on an airless node breathes nothing at all, whatever it
        #: is carrying. The whole stretch is uncovered -- and settled by the
        #: choking below, so the stamp goes all the way.
        locked.air_at = moment
        await session.flush()
        return Breath(left=0.0, uncovered=hours)

    stacks = await stock.lock_items(session, await cylinders(session, locked))
    #: What the last stretch breathed and could not be charged for is asked
    #: for first. Down to the thousandth air is split into, never up:
    #: `amount()` rounds to the nearest and would take one the stretch had not
    #: earned. Flooring alone would be worse than the disease -- an error that
    #: cancelled would become one that always took -- which is why the shaving
    #: is kept rather than dropped.
    owed = need + float(locked.air_owed)
    want = float(on_grid(owed, ROUND_AMOUNT, ROUND_FLOOR))
    took = amount_float(await stock.consume(session, stacks, amount(want)))
    #: Exactly enough must not read as short: amounts are split into
    #: thousandths, and the last digit of an hour's draw is rounding, not a
    #: gasp. The same tolerance the fuel check uses before a passage.
    missing = owed - took
    if missing > _EPS:
        #: A real shortage. The body choked for it and is not billed twice:
        #: nothing is carried on top of choking.
        locked.air_owed = Decimal(0)
    else:
        #: The breath the cylinder could not be asked for. It waits on the
        #: body, not on the stamp: this stretch may have ended aboard, and
        #: arriving in air moves the stamp to now -- which would forgive the
        #: debt every time a body stepped back up its own gangway.
        locked.air_owed = on_grid(max(0.0, missing), ROUND_REMAINDER, ROUND_FLOOR)
    locked.air_at = moment
    await session.flush()
    #: Asked again rather than summed off the stacks in hand: a stack spent to
    #: nothing is **deleted** by `consume`, and its object keeps the amount it
    #: had -- the sum would count air that no longer exists.
    left = await carried(session, locked)
    return Breath(left=left, uncovered=missing / draw if missing > _EPS and draw > 0 else 0.0)


async def tick_bodies(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    *,
    now: datetime | None = None,
) -> int:
    """Settle every body standing where there is no air; kill the ones it ran out on.

    Returns how many died. Bodies aboard are not here: their air is the hull's,
    and `tick_ships` settles them by the hull.
    """
    moment = now or datetime.now(UTC)
    airless = await airless_planets(session)
    if not airless:
        return 0
    bodies = (
        (
            await session.execute(
                select(Body)
                .join(Node, Node.id == Body.node_id)
                .where(
                    Body.state == BodyState.ALIVE,
                    Node.planet.in_([planet.value for planet in airless]),
                )
            )
        )
        .scalars()
        .all()
    )
    dead = 0
    for found in bodies:
        node = await session.get(Node, found.node_id)
        if node is None or vessels.is_aboard(node):  # pragma: no cover -- the hull's business
            continue
        breath = await settle(session, constants, catalog, found, now=moment)
        if breath.uncovered <= 0:
            #: Breathing again gives the grace back. Without this a body that
            #: once ran dry and then refilled would carry the mark to its death
            #: and be killed on the first incomplete stretch, with none of the
            #: settling of warning this module promises.
            await _breathing(session, found)
            continue
        if await _choked(session, constants, found, now=moment):
            dead += 1
    return dead


async def _choked(
    session: AsyncSession, constants: Constants, body: Body, *, now: datetime
) -> bool:
    """One settling of grace, then death.

    A stretch the reserve only half covered drains it and kills nobody: the
    tick that lands a second after the last unit is spent must not be
    indistinguishable from suffocation. The next stretch begins with nothing,
    and that one ends the body.
    """
    if await carried(session, body) > _EPS:
        return False
    if body.choking_since is None:
        body.choking_since = now
        await session.flush()
        return False

    from src.engine import death  # noqa: PLC0415 -- lazy: breaks the cycle with death

    await death.die(session, constants, body, cause=ASPHYXIA, now=now)
    return True


async def _breathing(session: AsyncSession, body: Body) -> None:
    """The body has air again: the grace is given back."""
    if body.choking_since is not None:
        body.choking_since = None
        await session.flush()


# --- the hull's own hours ------------------------------------------------------


async def tick_ships(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    *,
    now: datetime | None = None,
) -> tuple[float, int]:
    """Every sealed hull breathes its stretch. Returns (air breathed, crew lost).

    A hull is settled once for its whole crew: the draw is a number of people
    times an hourly rate, and asking it body by body would read the same
    vessels once a head.
    """
    moment = now or datetime.now(UTC)
    #: Only the hulls with a stretch to settle: a fleet grows with the players,
    #: and a tick that walked all of it every minute to write the same stamp
    #: back would be the cost of owning a shipyard.
    afloat = (
        (await session.execute(select(Ship).where(Ship.air_at < moment).order_by(Ship.id)))
        .scalars()
        .all()
    )
    breathed = 0.0
    dead = 0
    open_hulls: list[uuid.UUID] = []
    for ship in afloat:
        if not await sealed(session, ship):
            #: A hull with the hatch open still moves its stamp: otherwise a
            #: month at a Terran pier would be charged to the line the moment
            #: it cast off. Gathered and written in one statement -- most of a
            #: world's ships stand at a pier, and each of them is not worth a
            #: round trip.
            open_hulls.append(ship.id)
            continue
        drawn, lost = await _breathe(session, constants, catalog, ship, now=moment)
        breathed += drawn
        dead += lost
    if open_hulls:
        await session.execute(update(Ship).where(Ship.id.in_(open_hulls)).values(air_at=moment))
        await session.flush()
    return breathed, dead


async def _breathe(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    now: datetime,
) -> tuple[float, int]:
    """One hull's stretch: breathe it off the life support's line, count the dead."""
    locked = (
        (
            await session.execute(
                select(Ship)
                .where(Ship.id == ship.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one()
    )
    hours = (now - locked.air_at).total_seconds() / SECONDS_PER_HOUR
    if hours <= 0:
        return 0.0, 0
    locked.air_at = now

    crew = await vessels.crew_of(session, locked)
    if not crew:
        #: Nobody aboard breathes nothing, and the life support has no reason
        #: to run: an empty hull in flight arrives with its tanks as it left.
        await session.flush()
        return 0.0, 0

    #: The hold, once: which systems stand there and which vessels their
    #: lines reach. It is a **reading**; the write-off below relocks its
    #: stacks by id under `FOR UPDATE`, so nothing is decided from it.
    hold = await lines.hold_of(session, locked)

    need = hull_draw(constants, len(crew)) * hours
    drawn = 0.0
    if need > _EPS:
        stacks = await stock.lock_items(
            session,
            await breathable_stacks(session, constants, catalog, locked, things=hold),
            ordered=True,
        )
        #: What was **actually** written off is what was breathed, not what
        #: the reading promised: another hand may have poured the cylinder out
        #: between the two, and a crew credited with air it never had would
        #: live through an hour it did not live through.
        drawn = amount_float(await stock.consume(session, stacks, amount(need)))
    short = max(0.0, need - drawn)
    await session.flush()

    if short <= _EPS:
        for member in crew:
            await _breathing(session, member)
        return drawn, 0

    #: The hull ran dry. One settling of grace, exactly as outside: a stretch
    #: only half covered kills nobody, and the next one begun on empty tanks
    #: does. The whole crew shares one hull, so it shares one countdown.
    dead = 0
    for member in crew:
        if member.choking_since is None:
            member.choking_since = now
            continue
        from src.engine import death  # noqa: PLC0415 -- lazy: breaks the cycle with death

        await death.die(session, constants, member, cause=ASPHYXIA, now=now)
        dead += 1
    await session.flush()
    if dead == 0:
        #: Said once, when the tanks first fail to cover the hour: the crew has
        #: one settling to do something about it, and a silent hull would make
        #: the deaths that follow arrive out of nowhere.
        #:
        #: Addressed to **everybody aboard**, not to the owner and the
        #: connector: a hired hand in the engine room is the one this warning is
        #: for, and the node it stands in is not the one the event is written
        #: at. `push` hands an event to every party named by a key ending in
        #: `_identity_id`, so the crew is named that way.
        aboard = {
            f"crew{seat}_identity_id": str(member.identity_id) for seat, member in enumerate(crew)
        }
        await events.record(
            session,
            EventKind.SHIP_AIRLESS,
            actor_identity_id=locked.owner_identity_id,
            node_id=locked.connector_node_id,
            ship_id=str(locked.id),
            name=locked.name,
            crew=len(crew),
            **aboard,
        )
    return drawn, dead
