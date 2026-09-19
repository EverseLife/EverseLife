# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The one-off catch-up of D-354: a world laid before keeps a node above each
planet, and the seed takes it away, finding a place for what it held.

Pinned: a hull moored to it comes out in orbit, a body standing on it goes
aboard, a thing lying on it goes aboard with it, a climb bound for it is
bound for the planet, a pier the hull last left that was the node is
forgotten -- and the node is gone. Run again, the step has nothing to do.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ship_kit import _flightworthy, _laid, _planet, _port, _shipwright
from src import seed_orbits
from src.constants import Catalog, Constants
from src.engine import jobs, travel, world
from src.engine.ship import sim
from src.models.job import Job, JobKind
from src.models.world import Layer, Node, Planet, Surface

#: The mark the seed laid on a planet's orbital node before D-354.
LEGACY = "orbit_node"


async def _legacy_orbit(session: AsyncSession, sphere: Node) -> Node:
    """The node as the old seed laid it: the void over the planet, marked."""
    return await world.create_node(
        session,
        f"{sphere.planet.value}.orbit",
        "Околопланетная орбита",
        area_m2=40,
        planet=sphere.planet,
        layer=Layer.SPACE,
        parent=sphere,
        properties={LEGACY: True},
    )


async def test_the_orbital_node_goes_and_what_it_held_finds_a_place(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    now = datetime.now(UTC)
    terra = await _planet(session)
    home = await _port(session, name="Космодром столицы")
    orbit = await _legacy_orbit(session, terra)

    #: A hull moored to the node, as D-245 moored one, with its owner aboard.
    _, owner = await _shipwright(session, home, foundations=2)
    moored = await _laid(session, constants, owner, home)
    climbing = await _laid(session, constants, owner, home, name="Вторая")
    await _flightworthy(session, constants, catalog, moored)
    connector = await session.get(Node, moored.connector_node_id)
    assert connector is not None
    await travel.disconnect(session, home, connector)
    await travel.connect(session, orbit, connector, base_seconds=1, surface=Surface.PAVED)
    moored.docked_node_id = orbit.id
    moored.berth = 1
    moored.left_node_id = home.id
    owner.node_id = connector.id

    #: A body out on the node, and a thing lying beside it.
    walker = await world.print_body(
        session, await world.create_identity(session, "Выходящий"), orbit
    )
    yard = await world.node_container(session, orbit)
    await world.grant_item(session, yard, "iron_ore", amount=3, origin="тест")

    #: The second hull in the air, climbing to the node.
    climbing.docked_node_id = None
    climbing.left_node_id = home.id
    await session.flush()
    climb = await jobs.enqueue(
        session,
        JobKind.SHIP_FLIGHT,
        now + timedelta(hours=1),
        payload={"ship": str(climbing.id), "to": str(orbit.id), "leg": "climb"},
        dedup_key=f"test.climb:{climbing.id}",
    )
    assert climb is not None
    await session.flush()

    report = await seed_orbits.orbits_gone(session, constants, now=now)
    assert report["orbits"] == 1 and report["hulls"] == 1 and report["legs"] == 1
    assert report["aboard"] == 1 and report["things"] == 1

    assert await session.scalar(select(Node).where(Node.id == orbit.id)) is None, "узла нет"
    #: The hull: in orbit round Terra, no node, no gangway, no berth.
    assert moored.docked_node_id is None and moored.berth is None
    assert await sim.orbiting(session, constants, moored, now=now) is not None
    assert not await travel.exits(session, constants, connector), "трапа в пустоту нет"
    #: The body aboard, the thing aboard with it.
    await session.refresh(walker)
    assert walker.node_id == connector.id, "вышедший вернулся на борт"
    hold = await world.node_container(session, connector)
    assert any(one.type_key == "iron_ore" for one in await world.contents(session, hold))
    #: The climb bound for the planet, over the pier it left.
    job = await session.get(Job, climb.id)
    assert job is not None
    assert job.payload["to"] == str(terra.id) and job.payload["over"] == str(home.id)

    #: And run again, nothing to do.
    again = await seed_orbits.orbits_gone(session, constants, now=now)
    assert again["orbits"] == 0


async def test_a_body_in_the_void_with_no_hull_to_go_to_dies_of_it(
    session: AsyncSession, constants: Constants
) -> None:
    """D-245 said a body left on the orbital node when the hulls went was
    death, not a trap: with no hull moored there, the catch-up keeps that
    word -- and the body lies on the planet's node, the void having no floor."""
    now = datetime.now(UTC)
    aurora = await _planet(session, Planet.AURORA)
    orbit = await _legacy_orbit(session, aurora)
    walker = await world.print_body(
        session, await world.create_identity(session, "Оставленный"), orbit
    )
    await session.flush()

    report = await seed_orbits.orbits_gone(session, constants, now=now)
    assert report["died"] == 1
    await session.refresh(walker)
    assert walker.died_at is not None and walker.node_id == aurora.id
