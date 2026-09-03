# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The crossing between worlds: the order, and the turn-back (D-289).

Cut out of `flight` when the sky became a simulation: the legs to and from
the ground stayed there -- hours by gravity, a job at the end -- and the
crossing became an order the helm flies tick by tick (`sim`). What this
module keeps is the two commands that lay such an order: `fly`, from the
parking circle or from a drift, and `turn_home`, the same order aimed back.
The legs' checks -- the gangway, the fitness, the mooring -- are borrowed from
`flight`, which is why `flight.recall` reaches over here lazily.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events
from src.engine.ship import sim
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
from src.engine.ship.physics import mass
from src.models.event import EventKind
from src.models.identity import Body
from src.models.ship import Ship
from src.models.world import Node
from src.units import HOURS_PER_DAY, ROUND_DV, ROUND_HOURS, ROUND_MASS, ROUND_RATIO


async def fly(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    ship: Ship,
    target: Node,
    *,
    hours: float | None = None,
    now: datetime | None = None,
    back: bool = False,
) -> datetime:
    """Cross to another planet's orbit -- flown, not tabled (D-289).

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

    `back` marks the order as a turn-back (D-242): the journal records a
    recall rather than a launch, the console keeps its button dark, and a
    second turn-back is refused. Returns the hour the console promises.
    """
    moment = now or datetime.now(UTC)
    await _commanded_by(session, body, ship)
    await session.refresh(ship, with_for_update=True)
    if not is_orbit(target):
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
        if target.planet is here.planet:
            raise TooFar(key="ship-already-over-planet", ship=ship.name)
    #: Every question a mooring is asked, and one more the others are not: a
    #: planet whose beacons have all gone out is a planet one may reach and
    #: never leave the orbit of (D-232) -- so the crossing is refused at this
    #: end, while there is still a choice to make.
    await _will_take(session, constants, target, why="dock")
    if not await _landable(session, constants, target.planet):
        raise NoPort(key="ship-nowhere-to-land", node=target.name)

    if hours is None:
        world = await sim.system(session, constants)
        offered = await sim.offers(
            session, constants, catalog, ship, world.body(target.planet.value), now=moment
        )
        if not offered:
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

    #: The plan is written onto the row from the parking circle the hull
    #: still sits on; the gangway comes off after, and casting off leaves
    #: the state where the plan put it.
    plan, fuel = await sim.depart(
        session,
        constants,
        catalog,
        ship,
        target,
        hours=hours,
        thrust_ratio=thrust_ratio,
        now=moment,
    )
    if here is not None and connector is not None:
        await _cast_off(session, ship, here, connector)
    if back:
        #: Marked as the way back (D-242): the console keeps the button dark,
        #: and a second turn-back is refused -- the hull is already going there.
        ship.course = {**(ship.course or {}), "back": True}
        await session.flush()
    arrives = datetime.fromisoformat(str((ship.course or {})["due_at"]))
    await events.record(
        session,
        EventKind.SHIP_RECALLED if back else EventKind.SHIP_LAUNCHED,
        actor_identity_id=body.identity_id,
        node_id=ship.connector_node_id if here is None else here.id,
        ship_id=str(ship.id),
        name=ship.name,
        leg=PASSAGE,
        to=target.key,
        hours=round(hours, ROUND_HOURS),
        #: What the plan will burn by its own delta-v: the tanks pay as the hull
        #: goes, and the journal names the price the order was given at.
        fuel=round(fuel, ROUND_MASS),
        mass=round(await mass(session, constants, catalog, ship), ROUND_MASS),
        ratio=round(thrust_ratio, ROUND_RATIO),
        arrives_at=arrives.isoformat(),
        dv=round(plan.dv, ROUND_DV),
    )
    return arrives


async def turn_home(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    ship: Ship,
    *,
    now: datetime,
) -> datetime:
    """Turn a flown crossing back (D-289): the same order as any, aimed at
    the planet the hull left, laid from where the hull actually is.

    Not a second arc costing what the first did (D-242's rule for the tabled
    passage): the sky is simulated now, and the way home is priced by where
    the hull is and where home will be -- an hour out, an hour's worth; half
    way, whatever the geometry says. The order under way is dropped first,
    so the new one is laid from a coasting state.
    """
    home = None if ship.left_node_id is None else await session.get(Node, ship.left_node_id)
    if home is None or not is_orbit(home):
        raise NoPort(key="ship-no-home-to-turn-to", ship=ship.name)
    ship.course = None
    await session.flush()
    return await fly(session, constants, catalog, body, ship, home, now=now, back=True)
