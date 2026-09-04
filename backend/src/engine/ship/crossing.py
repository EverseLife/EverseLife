# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The crossing between worlds: the orders under the sky (D-289).

Cut out of `flight` when the sky became a simulation: the legs to and from
the ground stayed there -- hours by gravity, a job at the end -- and the
crossing became an order the helm flies tick by tick (`sim`). What this
module keeps is the commands that lay or end such an order: `fly`, from the
parking circle or from a drift; `cancel`, the autopilot off and the hull
coasting; `circle_star`, the hull put onto the circle round the star
(2026-09-04). No turn-back under the sky: the tabled legs alone come back
(`flight.recall`). The legs' checks -- the gangway, the fitness, the
mooring -- are borrowed from `flight`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from src import sky
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events
from src.engine.ship import fate, hold, meet, sighting, sim
from src.engine.ship._base import (
    PASSAGE,
    Docked,
    InFlight,
    NoArc,
    NoPort,
    ShipError,
    TooFar,
    is_orbit,
)
from src.engine.ship.command import _commanded_by, _landable, _will_take
from src.engine.ship.flight import _cast_off, _fit, _leaving, _passage_of
from src.engine.ship.physics import mass, sky_days
from src.models.event import EventKind
from src.models.identity import Body
from src.models.ship import Ship
from src.models.world import Node
from src.units import (
    HOURS_PER_DAY,
    MINUTES_PER_HOUR,
    ROUND_DV,
    ROUND_HOURS,
    ROUND_MASS,
    ROUND_RATIO,
)


async def fly(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    ship: Ship,
    target: Node | Ship,
    *,
    hours: float | None = None,
    now: datetime | None = None,
) -> datetime:
    """Cross to another planet's orbit -- flown, not tabled (D-289) -- or go
    to meet another hull on its coast (wave 3).

    From the parking circle over one planet to the parking circle over
    another, or from wherever inertia left a hull that has fuel again. The
    order is a point of the slider: `hours` from the fastest the engines
    deliver to the cheapest the horizon offers, unnamed the cheapest. The
    sky plans it as D-271 priced it -- a Lambert arc, the burns at both ends
    -- and then flies the chosen point under all five bodies (`sim.depart`);
    from there the helm re-solves the passage every tick from where the hull
    actually is, and the tanks pay as the engines burn (`sim.tick_sky`).

    Refused for what is impossible now and for nothing else: no thrust, no
    life support, a dark planet at the far end, an arc the sky does not
    offer or the engines cannot deliver, no fuel for the departure burn. The
    arrival burn is the console's warning: a hull short of it goes adrift
    rather than being kept at the pier, and adrift is a place one may be
    fetched from (D-289).

    Not turned back (D-289, 2026-09-04): a crossing under the sky is
    cancelled into a coast, or replaced by another order. Returns the hour
    the console promises.
    """
    moment = now or datetime.now(UTC)
    await _commanded_by(session, body, ship)
    await session.refresh(ship, with_for_update=True)
    meeting = isinstance(target, Ship)
    if not meeting and not is_orbit(target):
        raise NoPort(key="ship-cross-to-orbit", node=target.name)
    adrift = ship.docked_node_id is None
    here = connector = None
    if adrift:
        #: Under an order, on a leg, or lost: no new order. Only a hull that
        #: coasts -- a state and no order -- may be sent somewhere.
        if ship.lost_at is not None:
            raise ShipError(key="ship-lost", ship=ship.name)
        if ship.course or ship.sky_at is None:
            raise InFlight(key="ship-in-flight", ship=ship.name)
        if await _passage_of(session, ship) is not None:  # pragma: no cover -- legs have no state
            raise InFlight(key="ship-in-flight", ship=ship.name)
        thrust_ratio = await _fit(session, constants, catalog, ship)
    else:
        here, connector, thrust_ratio = await _leaving(session, constants, catalog, ship)
        if not is_orbit(here):
            raise Docked(key="ship-cross-from-orbit", ship=ship.name)
        if not meeting and target.planet is here.planet:
            raise TooFar(key="ship-already-over-planet", ship=ship.name)
    if isinstance(target, Ship):
        #: A hull as the target: in sight, coasting, on nobody's hold, and
        #: with a forecast to be met on (wave 3).
        await sighting.aimable(session, constants, ship, target, now=moment)
    else:
        #: Every question a mooring is asked, and one more the others are
        #: not: a planet whose beacons have all gone out is a planet one may
        #: reach and never leave the orbit of (D-232) -- so the crossing is
        #: refused at this end, while there is still a choice to make.
        await _will_take(session, constants, target, why="dock")
        if not await _landable(session, constants, target.planet):
            raise NoPort(key="ship-nowhere-to-land", node=target.name)

    offered = None
    if hours is None:
        world = await sim.system(session, constants)
        goal: sky.Target | None
        if isinstance(target, Ship):
            goal = await sim.drifter_of(session, constants, target)
            if goal is None:
                raise NoArc(key="ship-target-unknown")
        else:
            goal = world.body(target.planet.value)
        offered = await sim.offers(
            session, constants, catalog, ship, goal, now=moment, thrust_ratio=thrust_ratio
        )
        if not offered:
            if isinstance(target, Ship):
                raise TooFar(key="ship-no-route-to-ship", other=target.name)
            if here is None:
                raise TooFar(key="ship-no-route-adrift", planet_to=target.planet.value)
            raise TooFar(
                key="ship-no-such-route",
                planet_from=here.planet.value,
                planet_to=target.planet.value,
            )
        hours = min(offered, key=lambda one: one.dv).hours
    limit = float(constants[R.ORBIT_LONGEST_DAYS]) * HOURS_PER_DAY
    if not hours > 0 or hours > limit:
        raise NoArc(key="ship-hours-out-of-range", hours=round(hours, ROUND_HOURS), limit=limit)

    #: Whoever holds on to this hull is let go of first, from the state
    #: they shared (wave 3); then the plan is written onto the row from the
    #: parking circle the hull still sits on; the gangway comes off after,
    #: and casting off leaves the state where the plan put it.
    await hold.release_holders(
        session, constants, await sim.system(session, constants), ship, now=moment
    )
    plan, fuel = await sim.depart(
        session,
        constants,
        catalog,
        ship,
        target,
        hours=hours,
        thrust_ratio=thrust_ratio,
        now=moment,
        offered=offered,
    )
    if here is not None and connector is not None:
        await _cast_off(session, ship, here, connector)
    #: Off a hold or a docking (wave 3): the edge to the other hull comes off
    #: the way the gangway does, and the pair parts.
    await meet.let_go(session, constants, ship)
    arrives = datetime.fromisoformat(str((ship.course or {})["due_at"]))
    await events.record(
        session,
        EventKind.SHIP_LAUNCHED,
        actor_identity_id=body.identity_id,
        node_id=ship.connector_node_id if here is None else here.id,
        ship_id=str(ship.id),
        name=ship.name,
        leg=PASSAGE,
        to=target.name if isinstance(target, Ship) else target.key,
        #: The plan's hours, not the command's: to a hull the order takes
        #: the quote of its own moment, whatever the console read.
        hours=round(plan.hours, ROUND_HOURS),
        #: What the plan will burn by its own delta-v: the tanks pay as the hull
        #: goes, and the journal names the price the order was given at.
        fuel=round(fuel, ROUND_MASS),
        mass=round(await mass(session, constants, catalog, ship), ROUND_MASS),
        ratio=round(thrust_ratio, ROUND_RATIO),
        arrives_at=arrives.isoformat(),
        dv=round(plan.dv, ROUND_DV),
    )
    return arrives


async def cancel(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    ship: Ship,
    *,
    now: datetime | None = None,
) -> None:
    """Drop the course under way (D-289, 2026-09-04): the autopilot off, and
    that is all -- the hull coasts from where it is, a drifter like any
    other, its inertia counted, its loss booked and its owner told why. No
    fuel is spent; a new course may be laid from the drift."""
    moment = now or datetime.now(UTC)
    await _commanded_by(session, body, ship)
    await session.refresh(ship, with_for_update=True)
    if ship.lost_at is not None:
        raise ShipError(key="ship-lost", ship=ship.name)
    if not ship.course or ship.sky_at is None:
        raise InFlight(key="ship-no-course-to-cancel", ship=ship.name)
    world = await sim.system(session, constants)
    #: The seconds since the last tick are coasted, not burnt: the helm's
    #: minute is the tick's, and the order ends where the hull is.
    t0 = await sky_days(session, ship.sky_at)
    t1 = max(t0, await sky_days(session, moment))
    r0, v0 = sim._state_of(ship)
    step = float(constants[R.ORBIT_PLAN_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY
    rr, vv = sky.advance(
        world, np.array([t0]), np.array([t1]), np.array([r0]), np.array([v0]), dt_max=step
    )
    r, v = sim._row(rr), sim._row(vv)
    #: Whoever still holds on -- a holder the order's release skipped under
    #: somebody's hand, and the sweep has not reached -- is let go now,
    #: before this hull is a drifter they could be flying as one with.
    await hold.release_holders(session, constants, world, ship, now=moment)
    sim._write_state(ship, r, v, at=moment)
    ship.course = None
    await fate._adrift(session, constants, ship, world, now=moment, t=t1, r=r, v=v, why="cancelled")
    await session.flush()


async def circle_star(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    ship: Ship,
    *,
    now: datetime | None = None,
) -> datetime:
    """Put the hull onto the circle round the star through its own place
    (D-289, 2026-09-04): an order the helm flies at full thrust until the
    hull moves as a planet does at that radius, the tanks paying as it
    burns. Given from a drift or from under any order, which it replaces.
    Refused off a pier -- the star's orbit is entered from space -- and for
    a burn the tanks cannot pay for whole. Returns the hour the burn is due
    to end."""
    moment = now or datetime.now(UTC)
    await _commanded_by(session, body, ship)
    await session.refresh(ship, with_for_update=True)
    if ship.lost_at is not None:
        raise ShipError(key="ship-lost", ship=ship.name)
    if ship.docked_node_id is not None:
        raise Docked(key="ship-orbit-only-in-space", ship=ship.name)
    if ship.sky_at is None:
        #: On a tabled leg -- the climb or the descent: no state in the sky
        #: to lay the circle from, and the leg takes no orders (D-245).
        raise InFlight(key="ship-in-flight", ship=ship.name)
    if ship.course and ship.course.get("target") == sky.STAR.key:
        raise InFlight(key="ship-already-circling", ship=ship.name)
    thrust_ratio = await _fit(session, constants, catalog, ship)
    world = await sim.system(session, constants)
    #: The circle from here, coasted ahead: one that passes through a
    #: planet's hold is a circle into the planet, and is refused before the
    #: burn rather than booked as a loss after it.
    found = await sim.state_at(session, constants, ship, now=moment)
    if found is not None:
        r, _, t = found
        wanted = sky.star_circle(world, r)
        verdict = await fate.fate_of(session, constants, world, t, r, (wanted[0], wanted[1]))
        if verdict.kind == sky.CRASH:
            raise NoPort(key="ship-orbit-crosses-planet", body=verdict.body)
    await hold.release_holders(session, constants, world, ship, now=moment)
    plan, fuel = await sim.circle(
        session, constants, catalog, ship, thrust_ratio=thrust_ratio, now=moment
    )
    await meet.let_go(session, constants, ship)
    arrives = datetime.fromisoformat(str((ship.course or {})["due_at"]))
    await events.record(
        session,
        EventKind.SHIP_LAUNCHED,
        actor_identity_id=body.identity_id,
        node_id=ship.connector_node_id,
        ship_id=str(ship.id),
        name=ship.name,
        leg=PASSAGE,
        to=sky.STAR.key,
        hours=round(plan.hours, ROUND_HOURS),
        fuel=round(fuel, ROUND_MASS),
        mass=round(await mass(session, constants, catalog, ship), ROUND_MASS),
        ratio=round(thrust_ratio, ROUND_RATIO),
        arrives_at=arrives.isoformat(),
        dv=round(plan.dv, ROUND_DV),
    )
    return arrives
