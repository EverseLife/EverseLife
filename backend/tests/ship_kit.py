# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The shipyard the ship tests share: a planet in the sky, a port, a
shipwright, a laid keel, equipment and fuel aboard, a hull fit to fly. Used by the ship files
(`test_ship*.py`); not collected by pytest.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import sky
from src.constants import Catalog, Constants, current, current_catalog
from src.engine import ship, storage, travel, world
from src.engine.ship import fate, hold, lines, sim
from src.models.estate import Building
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.job import JobState
from src.models.ship import Ship
from src.models.world import Layer, Node, Planet

ENGINE = "engine_class_1"

LIFE = "life_support_system"

FUEL = "rocket_fuel"

TANK = "fuel_tank"

CONSOLE = "ship_console"


def orbit_marks(planet: Planet) -> dict:
    """The seed's orbit for one world, as `world.orbit_of` reads it.

    The vault's own since 2026-09-08 (`sky.circle_of`): a year from
    `orbit.period_days`, a radius from it by Kepler. Asked for rather than
    laid out at import, because a constant set has to be loaded first.
    """
    radius, period, phase = sky.circle_of(current(), planet.value)
    return {
        world.ORBIT_RADIUS: radius,
        world.ORBIT_PERIOD: period,
        world.ORBIT_PHASE: phase,
    }


async def _planet(session: AsyncSession, planet: Planet = Planet.TERRA) -> Node:
    """The planet's own node, with its orbit round the star (D-271): what a
    crossing is bound for, and what a hull in orbit hangs under (D-354).

    Fetch-or-create, because every port of a planet wants the same one.
    """
    return (await select_node(session, planet.value)) or await world.create_node(
        session,
        planet.value,
        planet.value.title(),
        area_m2=1,
        planet=planet,
        layer=Layer.SPACE,
        #: The seed's orbit (D-271): a passage is a Lambert arc between two
        #: orbits, and a planet without one is a planet nothing crosses to.
        properties={world.ORBIT: orbit_marks(planet)},
    )


async def select_node(session: AsyncSession, key: str) -> Node | None:
    return (await session.execute(select(Node).where(Node.key == key))).scalars().first()


#: Where on the parking circle a test's hull is put, radians off the planet's
#: own heading -- rather than over the meridian of the pad it climbed from at
#: the hour it arrived (`flight.meridian`, D-354), which is the wall clock's in
#: a test: the crossing to Aurora would cast off from a fresh angle each run.
#: That angle decides the run: `_fast_sample` flies the first `ok` point of
#: the slider, which is by construction the arc the engines can barely
#: deliver -- for the hull `_flightworthy` fits out its delta-v comes within
#: two per cent of what the thrust gives over those hours (the margin is where
#: the slider's ten-per-cent grid falls against `course.deliverable`, so
#: another hull's is another number).
#:
#: What that angle decides, swept against the engine over the whole circle of
#: it, twenty-four headings, re-measured after D-316 finished the capture: all
#: twenty-four moor, every one of them four hours *before* the hour the console
#: promised (-4.0 to -4.2, the cheap end's known slack -- OQ-136). The angle no
#: longer decides *whether* the crossing closes; it decides *when*, and that by
#: a working day -- the same order from the same minute moors at 27 hours from
#: one heading and at 48 from another, because the circle turns the hull into a
#: different departure window. So the pin stays: a fresh `uuid4` every run would
#: hand the test a different hour each time, and the slack it needs on one
#: heading is twice what it needs on another. An earlier reading of this sweep
#: found one heading near 1.05 rad that never moored at all; that was the helm
#: settling onto a circle of the wrong radius, and it is gone.
#:
#: Off the planet's heading and not an absolute angle, as `test_ship_meet`
#: reads it: which way a hull leaves the circle decides whether a dry coast
#: lasts or plunges, and the planet's heading turns with its year.
#:
#: The pin is the kit's default, so no test observes the engine's own layout by
#: accident any more; the one that observes it on purpose asks for it by name
#: (`heading=None` in `test_ship_orbits.test_an_orbit_has_no_pier_to_queue_at`).
PARK_HEADING = 0.0

#: How late a crossing may moor and still be the crossing that was ordered.
#: A pinned angle only holds while the vault's numbers stay put: retune
#: `orbit.thrust_scale` or `orbit.slider_step` and the pin and the band move
#: apart, so the tests that fly to a mooring say what they expect rather than
#: leaning on the slack in `_flown`.
#:
#: Eight, and not the two the first wave measured: the arrival now falls to
#: the circle before it brakes, and the promised hour counts that fall in two
#: shapes (OQ-136) rather than one factor. The middle of the slider is then
#: exact to a tenth of an hour; the two ends are still about five out, and in
#: opposite directions -- the fast end late, the cheap end early -- which is
#: the crudeness of "the fall takes the way left over the speed" and not a
#: systematic lie any more. Drawn above the measured worst of 5.2 so the check
#: keeps catching what it was written for: a passage that does not close at
#: all, which was thirty to ninety hours out.
LATE_HOURS = 8.0


async def _heading_of(
    session: AsyncSession, constants: Constants, planet: Planet, at: datetime
) -> float:
    """The planet's own heading on its solar orbit at `at`, radians.

    Read at the hull's own stamp rather than at the wall clock, so that a run
    is a run: a planet's year is short (Terra's is twenty-eight days), and the
    climb alone turns Terra some five hundredths of a radian.
    """
    world_ = await ship.sim.system(session, constants)
    _, speed = sky.place(world_.body(planet.value), await ship.sky_days(session, at))
    return math.atan2(float(speed[0, 1]), float(speed[0, 0]))


async def _in_orbit(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    vessel: Ship,
    *,
    heading: float | None = PARK_HEADING,
) -> Ship:
    """Climb and arrive: the hull in orbit round the planet it set out from,
    `heading` radians off that planet's own heading on the circle -- or, with
    `heading=None`, over the pad's meridian, where the climb puts it (see
    `PARK_HEADING`)."""
    job = await ship.ascend(session, constants, catalog, body, vessel)
    await ship.arrived(session, job)
    #: The climb is run by hand here, so close it by hand too: left pending it
    #: is a passage still under way, and the next order would be refused.
    job.state = JobState.DONE
    job.finished_at = job.run_at
    if heading is not None:
        #: Said out loud rather than guarded around: a climb that did not end
        #: on a circle is the caller's mistake, and a pin quietly skipped would
        #: hand the angle back to the id -- the lottery, in the one place
        #: nobody would look for it.
        assert vessel.sky_at is not None and vessel.docked_node_id is None, (
            "climb ended off the circle"
        )
        await _on_the_circle(session, constants, vessel, at=vessel.sky_at, heading=heading)
    await session.flush()
    return vessel


async def _on_the_circle(
    session: AsyncSession,
    constants: Constants,
    vessel: Ship,
    *,
    at: datetime,
    heading: float = PARK_HEADING,
) -> Ship:
    """The hull put on the parking circle of the planet its pad is on, at
    `at`, `heading` radians off that planet's own heading -- in orbit, with
    its forecast kept, whatever it was doing before. No climb, no fuel: for
    the tests that only need a hull to be up there."""
    world_ = await ship.sim.system(session, constants)
    planet = (await session.get(Node, vessel.node_id)).planet
    t = await ship.sky_days(session, at)
    r, v = sky.parking(
        world_,
        world_.body(planet.value),
        t,
        await _heading_of(session, constants, planet, at) + heading,
    )
    here = (float(r[0, 0]), float(r[0, 1]))
    speed = (float(v[0, 0]), float(v[0, 1]))
    vessel.docked_node_id = None
    vessel.berth = None
    sim._write_state(vessel, here, speed, at=at)
    sim._keep_forecast(
        vessel, await fate.fate_of(session, constants, world_, t, here, speed), now=at, t=t
    )
    await session.flush()
    return vessel


async def _port(session: AsyncSession, *, name: str = "Космодром", planet=Planet.TERRA):
    """A node with a spaceport: everything a ship starts from."""
    stamp = uuid.uuid4().hex[:8]
    await _planet(session, planet)
    node = await world.create_node(session, f"terra.port.{stamp}", name, area_m2=400, planet=planet)
    #: The yard's roof and no more: the rest of the node is the apron the
    #: hulls set down on (D-319), and a port roofed over would take none.
    session.add(Building(node_id=node.id, area_m2=80))
    await session.flush()
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "space_shipyard", quality=60, origin="тест")
    return node


async def _shipwright(session: AsyncSession, node: Node, *, foundations: int = 1):
    identity = await world.create_identity(session, f"Корабел-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    if foundations:
        pocket = await world.body_container(session, body)
        await world.grant_item(
            session, pocket, "ship_node_foundation", amount=foundations, origin="тест"
        )
    return identity, body


async def _laid(
    session: AsyncSession, constants: Constants, body: Body, port: Node, name="Заря"
) -> Ship:
    """Lay the foundation and run the work to its end -- a ship in port."""
    job = await ship.found(session, constants, body, name)
    await ship.keel_laid(session, job)
    #: The keel job is done by hand here, so close it by hand too: left pending
    #: it stays in the queue and a later `run_one` takes it instead of the
    #: flight it was called for -- the journal hands out the earliest ready job.
    job.state = JobState.DONE
    job.finished_at = job.run_at
    await session.flush()

    mine = await ship.ships_of(session, body.identity_id)
    assert mine, "закладка кончилась кораблём"
    return mine[-1]


async def _equip(session: AsyncSession, node: Node, type_key: str, amount: float = 1):
    yard = await world.node_container(session, node)
    return await world.grant_item(session, yard, type_key, amount=amount, quality=60, origin="тест")


async def _fuel(session: AsyncSession, node: Node, amount: float):
    """Fuel aboard is fuel in a tank (D-230), and a tank on the engines' line:
    a port without a line draws from nothing (D-288 as amended 2026-09-04),
    so every engine already aboard gets this tank appended to its fuel line,
    and a hull a test fuels is a hull it can fly."""
    tank = await _equip(session, node, TANK)
    inside = await storage.inside(session, tank)
    fuel = await world.grant_item(session, inside, FUEL, amount=amount, quality=60, origin="тест")
    hull = (
        await session.execute(select(Ship).where(Ship.node_id == node.parent_id))
    ).scalar_one_or_none()
    if hull is not None:
        for machine in await lines.hold_of(session, hull):
            if lines.port_of(current(), current_catalog(), machine.type_key, "fuel") is not None:
                rows = await lines.lines_of(session, machine.id, "fuel")
                await lines.replace(
                    session, machine, "fuel", [*(row.vessel_item_id for row in rows), tank.id]
                )
    return fuel


async def _flightworthy(
    session: AsyncSession, constants: Constants, catalog: Catalog, ship_: Ship
) -> None:
    """The minimum that tears off: an engine, life support, a console and fuel in a tank."""
    connector = await session.get(Node, ship_.connector_node_id)
    await _equip(session, connector, ENGINE)
    await _equip(session, connector, LIFE)
    await _equip(session, connector, CONSOLE)
    await _fuel(session, connector, 200)


async def _body_of(session: AsyncSession, vessel: Ship) -> Body:
    """The ship's owner's body -- the only one that may command it."""
    from sqlalchemy import select as sql_select

    return (
        (
            await session.execute(
                sql_select(Body).where(Body.identity_id == vessel.owner_identity_id)
            )
        )
        .scalars()
        .one()
    )


async def _fast_sample(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    vessel: Ship,
    planet: Planet,
    *,
    now: datetime | None = None,
) -> dict:
    """The fastest direct arc the slider offers to `planet` (D-341).

    The one a test flies: the tick steps the sky a minute at a time (D-289),
    and the horizon's twelve days of the cheapest arc is not a test
    (`orbit.longest_days`, D-271 as reset by D-317). It is also the arc with
    the least room in it -- near the slider's fast end the thrust barely
    covers the arc -- so a test that flies it and waits for the mooring
    reads the sky at the hour it casts off from (`now`) and departs from a
    pinned place on the circle (`PARK_HEADING`), or it is a different passage
    every run.
    """
    forecast = await ship.forecast(session, constants, catalog, vessel, planet, now=now)
    #: A direct arc, the fastest the slider offers: a flyby there would be
    #: another helm's test (`test_ship_flyby`).
    return next(one for one in forecast["samples"] if "via" not in one)


async def _flown(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    vessel: Ship,
    *,
    since: datetime,
    until: datetime,
    step: timedelta = timedelta(hours=1),
    slack: timedelta = timedelta(hours=24),
) -> datetime:
    """Tick the sky from `since` past `until`, an hour at a time, until the
    order is over -- the hull moored, or adrift (D-289). Returns the hour of
    the last tick."""
    now = since
    while now < until + slack:
        now += step
        await ship.helm.tick_sky(session, constants, catalog, now=now)
        await session.refresh(vessel)
        if vessel.docked_node_id is not None or vessel.course is None:
            return now
    return now


# --- two hulls in the sky (D-289, wave 3) -----------------------------------
#
# The rescue these tests arrange over and over: a hull that ran dry on the way
# to Aurora, and another that goes out to it. Shared by `test_ship_meet` (the
# meeting, the docking, the sighting) and `test_ship_hold` (what the held pair
# does under a lock, a loss and a new order).

#: What is left in the tank of a hull sent out to run dry: units of fuel.
DROP = 2.0


async def _events(session: AsyncSession, kind: EventKind) -> list[Event]:
    return list((await session.execute(select(Event).where(Event.kind == kind))).scalars().all())


async def _joined(session: AsyncSession, constants: Constants, a: Node, b: Node) -> bool:
    """Whether an edge stands between the two nodes."""
    return any(one.node_id == b.id for one in await travel.exits(session, constants, a))


#: Where on Terra's circle the hulls of a rescue are put: radians off Terra's
#: own heading at the moment, not an absolute angle -- which way a hull leaves
#: the circle decides how long its dry coast lasts, and Terra's heading turns
#: with the year. A drifter sent off from `DRIFTER_HEADING` coasts for weeks;
#: the rescuer sits a little behind it.
#:
#: Measured against the order these tests actually give, not derived: `_hull`
#: pins the angle at the hull's stamp, `_drifting` casts off at the wall clock
#: an ascent earlier, and the circle turns between the two. Move either hour
#: and both numbers want measuring again. A heading that plunges is no longer
#: among them: since D-316 the helm may not steer a hull toward the world it
#: is leaving, so a doomed coast is arranged by hand (`_plunging`).
DRIFTER_HEADING = 2.5
RESCUER_HEADING = DRIFTER_HEADING + 0.8

#: A pair that is only ever two hulls: far enough apart on the circle to be
#: two places, near enough to be in each other's sight (the whole circle is).
FIRST_HEADING = PARK_HEADING
SECOND_HEADING = PARK_HEADING + 0.8


async def _hull(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    port: Node,
    *,
    fuel: float,
    heading: float = PARK_HEADING,
) -> tuple[Ship, Body]:
    """A flight-worthy hull of a fresh owner, in Terra's orbit -- `heading`
    radians off Terra's own heading on the circle (`PARK_HEADING`)."""
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    await _fuel(session, connector, fuel)
    owner.node_id = connector.id
    await session.flush()
    await _in_orbit(session, constants, catalog, owner, vessel, heading=heading)
    return vessel, owner


async def _plunging(
    session: AsyncSession, constants: Constants, vessel: Ship, *, now: datetime
) -> sky.Fate:
    """Put a coasting hull on a line into Terra by hand: the verdict on its row
    and the loss booked, as the tick would have written them.

    Arranged rather than flown into (D-316): the helm may not steer a hull
    toward the world it is leaving, and a departure is where these tests used
    to get a plunge from.
    """
    world = await sim.system(session, constants)
    terra = world.body(Planet.TERRA.value)
    t = await ship.sky_days(session, now)
    p, vp = sky.place(terra, t)
    gap = sky.park_of(world, terra)
    here = (float(p[0, 0]) + gap, float(p[0, 1]))
    #: Straight at the centre at the circle's own speed: the ground in hours.
    falling = (float(vp[0, 0]) - float(np.sqrt(terra.mu / gap)), float(vp[0, 1]))
    sim._write_state(vessel, here, falling, at=now)
    verdict = await fate.book_loss(
        session, constants, vessel, world, now=now, t=t, r=here, v=falling
    )
    sim._keep_forecast(vessel, verdict, now=now, t=t)
    await session.flush()
    return verdict


async def _drifting(
    session: AsyncSession, constants: Constants, catalog: Catalog, vessel: Ship, owner: Body
) -> datetime:
    """Send the hull to Aurora and let it run dry on the way: adrift near
    Terra, with a forecast on its row. Returns the hour of the last tick."""
    aurora = await _planet(session, Planet.AURORA)
    moment = datetime.now(UTC)
    forecast = await ship.forecast(session, constants, catalog, vessel, Planet.AURORA, now=moment)
    fast = next(one for one in forecast["samples"] if "via" not in one)
    await ship.fly(
        session, constants, catalog, owner, vessel, aurora, hours=fast["hours"], now=moment
    )
    aboard = await ship.fuel_aboard(session, constants, catalog, vessel)
    await ship._spend(
        session, await ship.fuel_stacks(session, constants, catalog, vessel), aboard - DROP
    )
    await session.flush()
    last = await _flown(
        session, constants, catalog, vessel, since=moment, until=moment + timedelta(hours=12)
    )
    assert vessel.course is None and vessel.forecast is not None, "в дрейфе, с прогнозом"
    return last


async def _met(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> tuple[Ship, Body, Ship, Body, datetime]:
    """A drifter and a rescuer of another owner that came to rest beside it."""
    home = await _port(session, name="Космодром столицы")
    await _port(session, name="Космодром Мерида", planet=Planet.AURORA)
    drifter, lost_owner = await _hull(
        session, constants, catalog, home, fuel=5000, heading=DRIFTER_HEADING
    )
    last = await _drifting(session, constants, catalog, drifter, lost_owner)
    rescuer, rescuer_owner = await _hull(
        session, constants, catalog, home, fuel=5000, heading=RESCUER_HEADING
    )
    #: A foreign hull is aimed at only in sight: the drifter went adrift a
    #: few units off Terra's circle, and the rescuer sits on it.
    #: One price to a hull, and no slider: the approach profile's own.
    since = last + timedelta(minutes=1)
    forecast = await ship.forecast(session, constants, catalog, rescuer, drifter, now=since)
    (quote,) = forecast["samples"]
    assert quote["hours"] > 0 and quote["dv"] > 0
    #: Read at one moment and ordered five minutes later: the quote moves
    #: with the geometry, and the order takes the one of its own moment
    #: rather than looking the console's up and missing it.
    ordered = since + timedelta(minutes=5)
    await ship.fly(
        session,
        constants,
        catalog,
        rescuer_owner,
        rescuer,
        drifter,
        hours=quote["hours"],
        now=ordered,
    )
    assert rescuer.course is not None and rescuer.course["ship"] == str(drifter.id)
    #: The quote of the order's own moment: a hull plunging toward a planet
    #: is a different geometry five minutes on, and the console's number is
    #: not looked up and missed.
    assert rescuer.course["hours"] > 0 and rescuer.course["dv"] > 0
    until = ordered + timedelta(hours=rescuer.course["hours"])
    at = await _flown(
        session, constants, catalog, rescuer, since=ordered, until=until, slack=timedelta(hours=48)
    )
    assert rescuer.held_ship_id == drifter.id, "рулевой встал рядом и держится"
    #: And in SQL, too, nobody of the two is under an order and the pair is
    #: no orphan: a Python None once went into the JSON column as the JSON
    #: value `null`, and every `course IS NOT NULL` took every drifter for
    #: an ordered hull -- the tick locked them all, the sweep filtered none.
    under_orders = (await session.execute(select(Ship.id).where(Ship.course.isnot(None)))).all()
    assert under_orders == [], "JSON null не SQL NULL"
    assert (await session.execute(hold.orphaned_holds())).all() == []
    assert (await session.execute(hold.half_docks())).all() == []
    return drifter, lost_owner, rescuer, rescuer_owner, at


async def _orbiting(session: AsyncSession, constants: Constants, vessel: Ship) -> str | None:
    """The planet the hull is in orbit round, read at its own stamp (D-354):
    what the console calls "in orbit" -- there is no node to be moored to."""
    if vessel.sky_at is None:
        return None
    body = await sim.orbiting(session, constants, vessel, now=vessel.sky_at)
    return None if body is None else body.key
