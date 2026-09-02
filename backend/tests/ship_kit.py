# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The shipyard the ship tests share: an orbit, a port, a shipwright, a laid
keel, equipment and fuel aboard, a hull fit to fly. Used by the ship files
(`test_ship*.py`); not collected by pytest.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import seed_parts
from src.constants import Catalog, Constants
from src.engine import ship, storage, world
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


async def _in_orbit(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body, vessel: Ship
) -> Ship:
    """Climb and arrive: the hull hanging over the planet it set out from."""
    job = await ship.ascend(session, constants, catalog, body, vessel)
    await ship.arrived(session, job)
    #: The climb is run by hand here, so close it by hand too: left pending it
    #: is a passage still under way, and the next order would be refused.
    job.state = JobState.DONE
    job.finished_at = job.run_at
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
    """Fuel aboard is fuel in a tank (D-230): a tank in the room, the fuel inside it."""
    tank = await _equip(session, node, TANK)
    inside = await storage.inside(session, tank)
    return await world.grant_item(session, inside, FUEL, amount=amount, quality=60, origin="тест")


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
