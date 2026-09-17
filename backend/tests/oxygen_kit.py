# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The airless bench the oxygen tests share (D-233, D-234, D-288): a planet
with or without air, ground on it, a port, a body, a hull with its owner
aboard, a life support system and its line, vessels with a liquid in them, a
suit and a cylinder. Used by `test_oxygen*.py`; not collected by pytest.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import gear, oxygen, ship, storage, world
from src.engine.ship import lines
from src.models.estate import Building
from src.models.identity import Body
from src.models.inventory import Item
from src.models.ship import Ship
from src.models.world import Layer, Node, Planet
from src.units import amount_float

AIR = "oxygen"
WATER = "water"
TANK = "fuel_tank"
CYLINDER = "oxygen_tank"
CANISTER = "canister"
CHEST = "chest"
SUIT = "heatproof_suit"
LIFE = "life_support_system"


async def _sphere(session: AsyncSession, planet: Planet, *, airless: bool) -> Node:
    """The planet's own node, where its properties live -- climate, air, landing."""
    node = await world.create_node(
        session,
        planet.value,
        planet.value.title(),
        area_m2=1,
        planet=planet,
        layer=Layer.SPACE,
        properties={oxygen.AIRLESS: True} if airless else {},
    )
    return node


async def _orbit(session: AsyncSession, sphere: Node) -> Node:
    """The void over a planet, laid under its sphere as the seed lays it (D-245):
    carrying the planet it circles, and none of its air."""
    return await world.create_node(
        session,
        ship.orbit_key(sphere.planet),
        f"Орбита {sphere.name}",
        area_m2=1,
        planet=sphere.planet,
        layer=Layer.SPACE,
        parent=sphere,
        properties={ship.ORBIT_NODE: True},
    )


async def _ground(session: AsyncSession, planet: Planet, sphere: Node, name="Поле") -> Node:
    node = await world.create_node(
        session,
        f"{planet.value}.field.{uuid.uuid4().hex[:8]}",
        name,
        area_m2=400,
        planet=planet,
        layer=Layer.PLANET,
        parent=sphere,
    )
    session.add(Building(node_id=node.id, area_m2=400))
    await session.flush()
    return node


async def _port(session: AsyncSession, planet=Planet.TERRA) -> Node:
    node = await world.create_node(
        session,
        f"{planet.value}.port.{uuid.uuid4().hex[:8]}",
        "Космодром",
        area_m2=400,
        planet=planet,
    )
    session.add(Building(node_id=node.id, area_m2=400))
    await session.flush()
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "space_shipyard", quality=60, origin="тест")
    return node


async def _person(session: AsyncSession, node: Node) -> Body:
    identity = await world.create_identity(session, f"Дышащий-{uuid.uuid4().hex[:6]}")
    return await world.print_body(session, identity, node)


async def _in_tank(session: AsyncSession, node: Node, what: str, amount: float) -> Item:
    """A liquid aboard lives in a vessel: a tank standing in the room, the liquid inside it."""
    yard = await world.node_container(session, node)
    tank = await world.grant_item(session, yard, TANK, quality=60, origin="тест")
    inside = await storage.inside(session, tank)
    await world.grant_item(session, inside, what, amount=amount, quality=60, origin="тест")
    return tank


async def _in_canister(
    session: AsyncSession, node: Node, what: str, amount: float, *, installed: bool = True
) -> Item:
    """A canister in the room, with a liquid in it. Installed, it stands on the
    lines like a tank (D-288); loose, it is luggage."""
    yard = await world.node_container(session, node)
    can = await world.grant_item(
        session, yard, CANISTER, quality=60, origin="тест", installed=installed
    )
    inside = await storage.inside(session, can)
    await world.grant_item(session, inside, what, amount=amount, quality=60, origin="тест")
    return can


async def _held(session: AsyncSession, box: Item) -> float:
    return sum(amount_float(one.amount) for one in await storage.content(session, box))


async def _cylinder(session: AsyncSession, body: Body, amount: float) -> None:
    """A cylinder in the hands, with air in it. Nothing is breathed from the bag itself."""
    pocket = await world.body_container(session, body)
    bottle = await world.grant_item(session, pocket, CYLINDER, quality=60, origin="тест")
    inside = await storage.inside(session, bottle)
    await world.grant_item(session, inside, AIR, amount=amount, quality=60, origin="тест")


async def _suited(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body
) -> None:
    """A suit on the body, not in the bag: the bag connects nothing (D-234)."""
    pocket = await world.body_container(session, body)
    suit = await world.grant_item(session, pocket, SUIT, quality=60, origin="тест")
    await gear.equip(session, constants, catalog, body, suit)


async def _plumb(session: AsyncSession, system: Item, *vessels: Item) -> None:
    """The life support's line, drawn by hand: a port without a line drinks
    from nothing (D-288 as amended 2026-09-04)."""
    await lines.replace(session, system, "oxygen", [one.id for one in vessels])


async def _system(session: AsyncSession, node: Node) -> Item:
    """A life support system standing in the room: what the air line hangs on (D-288)."""
    yard = await world.node_container(session, node)
    return await world.grant_item(session, yard, LIFE, quality=60, origin="тест")


async def _hull(session: AsyncSession, constants: Constants, port: Node) -> tuple[Ship, Body, Node]:
    """A ship in port with its owner standing aboard."""
    identity = await world.create_identity(session, f"Корабел-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, port)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "ship_node_foundation", origin="тест")
    job = await ship.found(session, constants, body, "Заря")
    await ship.keel_laid(session, job)
    vessel = (await ship.ships_of(session, identity.id))[-1]
    connector = await session.get(Node, vessel.connector_node_id)
    body.node_id = connector.id
    await session.flush()
    return vessel, body, connector
