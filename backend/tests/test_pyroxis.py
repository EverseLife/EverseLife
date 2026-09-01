# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The surface of Pyroxis: where one lands and what the ground allows (D-230,
D-233).

Every node of the surface takes a landing, the console shows the planet and
not its fields, ground without the planet's mark takes nobody, and nothing
grows where the rock bakes. The eruptions live in `test_pyroxis_eruption.py`,
the planet's clock in `test_pyroxis_clock.py`.
"""

from __future__ import annotations

import random
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pyroxis_kit import _surface
from src.constants import Constants, current_catalog
from src.engine import ship, world
from src.models.ship import Ship
from src.models.world import Layer, Node, Planet

# --- landing ------------------------------------------------------------------


async def test_every_node_of_the_surface_is_a_landing_site(
    session: AsyncSession, constants: Constants
) -> None:
    """Nothing is built on Pyroxis (D-230), so there is no yard to aim at -- and
    the planet takes a ship anywhere on its ground instead (D-233)."""
    plateau, fields = await _surface(session)
    landings = {node.key for node in await ship.open_landings(session)}
    assert plateau.key in landings
    assert {field.key for field in fields} <= landings

    #: The planet's own node is where it stands in the sky, not a place to put
    #: a hull down on -- and both answers say so, or a flight would be offered
    #: by one and refused by the other.
    assert "pyroxis" not in landings
    assert await ship.lands_anywhere(session, plateau)
    sphere = await session.scalar(select(Node).where(Node.key == "pyroxis"))
    assert sphere is not None
    assert not await ship.lands_anywhere(session, sphere)

    #: And every one of them is a destination: there is no beacon to go out.
    lit = {node.key for node in await ship.lit_ports(session, constants)}
    assert plateau.key in lit


async def test_the_console_shows_the_planet_and_not_every_field_of_it(
    session: AsyncSession, constants: Constants
) -> None:
    """A planet one lands anywhere on is one line of the console (D-233).

    Its fields differ in nothing the console can show -- same hours, same fuel,
    same class -- and their number grows with every field a scout opens: six
    identical rows today, sixty later, in a socket answer sent every time the
    console is opened (D-225).

    Asked of a hull **in orbit** over Pyroxis, because that is where the pad is
    chosen at all now (D-245): from the ground there is one move and it is the
    climb, and between worlds one goes orbit to orbit.
    """
    from src.engine.ship.view import profile

    plateau, fields = await _surface(session, count=6)
    #: `_surface` has already laid the planet: the orbit hangs under that one.
    sphere = await session.get(Node, plateau.parent_id)
    assert sphere is not None
    orbit = await world.create_node(
        session,
        ship.orbit_key(Planet.PYROXIS),
        "Околопланетная орбита Пироксиса",
        planet=Planet.PYROXIS,
        area_m2=1,
        layer=Layer.SPACE,
        parent=sphere,
        properties={ship.ORBIT_NODE: True},
    )
    owner = await world.create_identity(session, f"Капитан-{uuid.uuid4().hex[:6]}")
    hull = await world.create_node(
        session,
        f"ship.{uuid.uuid4().hex[:6]}",
        "Корабль",
        area_m2=1,
        planet=Planet.PYROXIS,
        layer=Layer.SPACE,
    )
    connector = await world.create_node(
        session,
        f"{hull.key}.connector",
        "Коннектор",
        area_m2=20,
        planet=Planet.PYROXIS,
        layer=Layer.LOCATION,
        parent=hull,
        properties={ship.ABOARD: True},
    )
    hulk = Ship(
        name="Вахта",
        owner_identity_id=owner.id,
        node_id=hull.id,
        connector_node_id=connector.id,
        docked_node_id=orbit.id,
    )
    session.add(hulk)
    await session.flush()

    console = await profile(session, constants, current_catalog(), hulk)
    assert console["stage"] == "orbit"
    assert len(console["landings"]) == 1, "консоль перечисляет планету, а не каждое её поле"
    row = console["landings"][0]
    #: And it says so, so the client knows a node picker belongs here.
    assert row["anywhere"] is True
    assert row["node"] in {plateau.key, *(field.key for field in fields)}
    #: A name and nothing else: what a descent costs is a fact about the planet,
    #: and it is sent once beside the list rather than copied into every field
    #: of it (D-225, D-245).
    assert set(row) == {"node", "name", "anywhere"}
    #: This hull has no engines at all, so the price is offered and unreachable
    #: rather than hidden: "не отрывается" is an answer, and a missing row is not.
    assert set(console["descent"]) == {"hours", "fuel", "needs", "reachable"}
    assert console["descent"]["reachable"] is False
    #: The name is the planet's own, not the field the row happens to carry:
    #: the hull comes down where the roll puts it (D-235).
    assert row["name"] == sphere.name


async def test_a_landing_without_a_port_falls_where_the_rock_allows(
    session: AsyncSession, constants: Constants
) -> None:
    """A planet with no ports takes a ship into a node of its own choosing
    (D-233, D-235).

    There is nothing to prefer: no piers, no berths, no lit beacons. So the
    node is rolled at the landing rather than picked in the console -- one sets
    down where the rock allows. Seeded by the job, so a flight that failed and
    is retried puts the hull in the same place instead of teleporting it across
    the planet on the second attempt.
    """
    from src.engine.ship.flight import _somewhere_on

    plateau, fields = await _surface(session, count=6)
    ground = {plateau.id, *(field.id for field in fields)}

    #: The same job always lands in the same place.
    twice = set()
    for _ in range(2):
        twice.add((await _somewhere_on(session, plateau, dice=random.Random("job-1"))).id)
    assert len(twice) == 1, "повтор рейса не должен переносить корабль"

    #: And across many flights the whole surface is used, not one node.
    where = set()
    for attempt in range(40):
        landed = await _somewhere_on(session, plateau, dice=random.Random(f"job-{attempt}"))
        assert landed.id in ground, "сели мимо планеты"
        where.add(landed.id)
    assert len(where) > 1, "садятся всегда в один узел — это не жеребьёвка"


async def test_ground_without_a_planet_property_takes_nobody(
    session: AsyncSession, constants: Constants
) -> None:
    """Landing anywhere is a property of the **planet** (D-233), not a hole in
    the rule: on Terra a ship still needs a yard."""
    wild = await world.create_node(
        session, f"terra.wild.{uuid.uuid4().hex[:6]}", "Пустошь", area_m2=100, layer=Layer.PLANET
    )
    assert not await ship.lands_anywhere(session, wild)
    assert wild.key not in {node.key for node in await ship.open_landings(session)}


async def test_nothing_grows_where_the_ground_bakes(
    session: AsyncSession, constants: Constants
) -> None:
    """A grove on a lava field would be a property nobody could explain
    (D-231, D-233): the search does not offer what the planet cannot hold."""
    from src.engine import explore

    _, fields = await _surface(session, count=1)
    offered = await explore.possible(session, fields[0])
    assert explore.VEIN in offered and explore.SITE in offered
    assert explore.FOREST not in offered
