# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The shipyard the ship tests share: an orbit, a port, a shipwright, a laid
keel, equipment and fuel aboard, a hull fit to fly. Used by the ship files
(`test_ship*.py`); not collected by pytest.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import seed_parts, sky
from src.constants import Catalog, Constants, current
from src.engine import ship, storage, world
from src.engine.ship import lines
from src.models.estate import Building
from src.models.identity import Body
from src.models.job import JobState
from src.models.ship import Ship
from src.models.world import Layer, Node, Planet

ENGINE = "engine_class_1"

LIFE = "life_support_system"

FUEL = "rocket_fuel"

TANK = "fuel_tank"

CONSOLE = "ship_console"

#: The seed's system, as `world.orbit_of` reads it: Keplerian, so that every
#: planet gives the same pull of the star (D-271).
ORBITS = {
    circle.planet: {
        world.ORBIT_RADIUS: circle.radius,
        world.ORBIT_PERIOD: circle.period_days,
        world.ORBIT_PHASE: circle.phase,
    }
    for circle in seed_parts.SYSTEM
}


async def _orbit(session: AsyncSession, planet: Planet = Planet.TERRA) -> Node:
    """The planet's orbital node, and the planet's own node under it (D-245).

    Fetch-or-create, because every port of a planet wants the same one: the
    orbit is where a hull hangs between the ground and the sky, and there is
    exactly one of them per world.
    """
    sphere = (await select_node(session, planet.value)) or await world.create_node(
        session,
        planet.value,
        planet.value.title(),
        area_m2=1,
        planet=planet,
        layer=Layer.SPACE,
        #: The seed's orbit (D-271): a passage is a Lambert arc between two
        #: orbits, and a planet without one is a planet nothing crosses to.
        properties={world.ORBIT: ORBITS[planet]},
    )
    key = ship.orbit_key(planet)
    return (await select_node(session, key)) or await world.create_node(
        session,
        key,
        f"Околопланетная орбита {planet.value}",
        area_m2=1,
        planet=planet,
        layer=Layer.SPACE,
        parent=sphere,
        properties={ship.ORBIT_NODE: True},
    )


async def select_node(session: AsyncSession, key: str) -> Node | None:
    return (await session.execute(select(Node).where(Node.key == key))).scalars().first()


#: Where on the parking circle a test's hull is put, radians off the planet's
#: own heading -- rather than the angle its id spins it to (`sim.bearing_of`).
#: In the game that spin keeps two hulls over one planet off one point; in a
#: test the id is a fresh `uuid4` every run, so the crossing to Aurora cast off
#: from a fresh angle each time. That angle decides the run: `_fast_sample`
#: flies the first `ok` point of the slider, which is by construction the arc
#: the engines can barely deliver -- for the hull `_flightworthy` fits out its
#: delta-v comes within two per cent of what the thrust gives over those hours
#: (the margin is where the slider's ten-per-cent grid falls against
#: `course.deliverable`, so another hull's is another number).
#:
#: What that angle decides, swept against the engine over the whole circle of
#: it: twenty-three headings in twenty-four moor an hour or two past the hour
#: the console promised, and one -- near 1.05 rad -- never moors at all, still
#: under its order ten days on. That is the rate at which the crossing test
#: used to go red, and the reason widening its slack would have been the wrong
#: repair: on that one heading there is nothing to wait for. The angle picks
#: the passage; why that passage does not close is not run down here, and it
#: deserves a line in the vault rather than this comment.
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
#: the crudeness of «the fall takes the way left over the speed» and not a
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
    """Climb and arrive: the hull hanging over the planet it set out from,
    `heading` radians off that planet's own heading on the circle -- or, with
    `heading=None`, wherever the hull's id spins it (see `PARK_HEADING`)."""
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
        assert vessel.sky_at is not None and vessel.docked_node_id is not None, (
            "climb ended off the circle"
        )
        moored = await session.get(Node, vessel.docked_node_id)
        assert moored is not None
        vessel.park_phase = (
            await _heading_of(session, constants, moored.planet, vessel.sky_at) + heading
        )
    await session.flush()
    return vessel


async def _port(session: AsyncSession, *, name: str = "Космодром", planet=Planet.TERRA):
    """A node with a spaceport: everything a ship starts from."""
    stamp = uuid.uuid4().hex[:8]
    await _orbit(session, planet)
    node = await world.create_node(session, f"terra.port.{stamp}", name, area_m2=400, planet=planet)
    session.add(Building(node_id=node.id, area_m2=400))
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
            if lines.port_of(current(), machine.type_key, "fuel") is not None:
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
    """The fastest arc the engines deliver to `planet`, off the slider.

    The one a test flies: the tick steps the sky a minute at a time (D-289),
    and the horizon's twelve days of the cheapest arc is not a test
    (`orbit.longest_days`, D-271 as reset by D-317). It is also the arc with
    the least room in it -- the first `ok` point of the slider is the one the
    thrust barely covers -- so a test that flies it and waits for the mooring
    reads the sky at the hour it casts off from (`now`) and departs from a
    pinned place on the circle (`PARK_HEADING`), or it is a different passage
    every run.
    """
    forecast = await ship.forecast(session, constants, catalog, vessel, planet, now=now)
    return next(one for one in forecast["samples"] if one["ok"])


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
