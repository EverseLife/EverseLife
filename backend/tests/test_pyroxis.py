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

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pyroxis_kit import _surface
from ship_kit import orbit_marks
from src import sky
from src.constants import Constants, current_catalog
from src.engine import estate, ship, world
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

    Asked of a hull **in orbit** round Pyroxis, because that is where the pad
    is chosen at all now (D-245): from the ground there is one move and it is
    the climb, and between worlds one goes orbit to orbit -- a place in the
    sky since D-354, not a node.
    """
    from src.engine.ship.view import profile

    plateau, fields = await _surface(session, count=6)
    #: `_surface` has already laid the planet; give it its year round the star
    #: so the sky runs it, and a hull can be in orbit round it (D-354).
    sphere = await session.get(Node, plateau.parent_id)
    assert sphere is not None
    sphere.properties = {**(sphere.properties or {}), world.ORBIT: orbit_marks(Planet.PYROXIS)}
    owner = await world.create_identity(session, f"Капитан-{uuid.uuid4().hex[:6]}")
    hull = await world.create_node(
        session,
        f"ship.{uuid.uuid4().hex[:6]}",
        "Корабль",
        area_m2=1,
        planet=Planet.PYROXIS,
        layer=Layer.SPACE,
        parent=sphere,
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
    )
    session.add(hulk)
    await session.flush()
    #: On Pyroxis' parking circle: a body in its sky, no node under it.
    sky_now = await ship.sim.system(session, constants)
    now = datetime.now(UTC)
    t = await ship.sky_days(session, now)
    r, v = sky.parking(sky_now, sky_now.body(Planet.PYROXIS.value), t, 0.0)
    ship.sim._write_state(
        hulk, (float(r[0, 0]), float(r[0, 1])), (float(v[0, 0]), float(v[0, 1])), at=now
    )
    await session.flush()

    console = await profile(session, constants, current_catalog(), hulk)
    assert console["stage"] == "orbit"
    #: Every node of the surface is a row: the globe under the hull picks
    #: among them (D-319 item 10), and each says how much ground is free.
    rows = {row["node"]: row for row in console["landings"]}
    assert set(rows) == {plateau.key, *(field.key for field in fields)}
    row = rows[plateau.key]
    #: And it says so, so the client knows the whole surface is a pad.
    assert row["anywhere"] is True
    #: The node's own name and its room, nothing else: what a descent costs
    #: is a fact about the planet, and it is sent once beside the list rather
    #: than copied into every field of it (D-225, D-245).
    assert set(row) == {"node", "name", "anywhere", "room"}
    assert row["name"] == plateau.name
    assert row["room"] == round(await estate.free_ground(session, plateau))
    #: What the hull needs of that room, once beside the list.
    assert console["footprint"] == round(await ship.hull_footprint(session, hulk))
    #: This hull has no engines at all, so the price is offered and unreachable
    #: rather than hidden: "не отрывается" is an answer, and a missing row is not.
    assert set(console["descent"]) == {"hours", "fuel", "needs", "reachable"}
    assert console["descent"]["reachable"] is False


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
