# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The surfaces of Pyroxis and Aurora: where a ship may land (D-230).

Split out of `seed.py` -- the starting world's module is long past the length
a file should have, and the other planets are content of their own.

A ship flies to a **spaceport** and nowhere else: the route list is the list
of nodes with a yard in them (`ship.ports`). So a planet exists for a pilot
only once something with a yard stands on it, and that is what the seed lays
down here: a landing on the Anvil Plateau of Pyroxis, and the ports of the
Forerunners' abandoned city on Aurora.

* **Pyroxis** -- one plateau, one spaceport. The plateau is a `planet`-layer
  node like the capital, but it is no city: nobody founded it, nobody owns it
  and nothing gets built on it (`estate.construct` refuses the planet). Its
  built-up layer is a **camp**, and the client names it so. Players may bring
  their own yards and found more.
* **Aurora** -- the abandoned cities of the Forerunners, `AURORA_CITIES` of
  them, each a `planet`-layer node of its own with one spaceport in it: a
  ship arriving at Aurora chooses which city to land at. The scouts there do
  not find empty lots, they find parts of a city that stood already -- the
  rest of every city is for that scouting to reveal. The yards are **relics**,
  not assembled by recipe the way the capital's machines are (D-216): nobody
  made them in this world, they were found --
  and assembling six hundred of them from raw stone would cost the seed a
  minute and a half for a provenance nobody asked for.

Idempotent like the rest of the seed: a node found by key is left alone,
and a world laid out before the planets had surfaces catches up on its own.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import current_catalog
from src.engine import ship, world
from src.models.world import Layer, Node, Planet

#: The Anvil Plateau: the one stable ground of Pyroxis (10-world/04).
PYROXIS_PLATEAU = "pyroxis.anvil"
PYROXIS_PORT = "pyroxis.anvil.port"

#: The Forerunners' cities under the ice of Aurora (10-world/05).
AURORA_RUINS = "aurora.ruins"
#: How many abandoned cities there are, one spaceport in each. A great many on
#: purpose: the race for Aurora is between cities of Terra, and a single pier
#: would make it a race for one berth.
AURORA_CITIES = 666

#: The area of a port node, the capital's own (D-206).
PORT_AREA_M2 = 240


def aurora_city_key(number: int) -> str:
    """The key of the n-th abandoned city, from 1."""
    return f"{AURORA_RUINS}.{number:03d}"


def aurora_port_key(number: int) -> str:
    """The key of the one spaceport of the n-th abandoned city."""
    return f"{aurora_city_key(number)}.port"


Machine = Callable[[AsyncSession, Node, str, float], Awaitable[None]]


async def surfaces(session: AsyncSession, machine: Machine) -> None:
    """Lay the surfaces of both planets. `machine` places a yard, assembled by recipe."""
    await _pyroxis(session, machine)
    await _aurora(session, machine)


async def _pyroxis(session: AsyncSession, machine: Machine) -> None:
    sphere = await _sphere(session, "pyroxis")
    plateau = await _ensure(
        session,
        PYROXIS_PLATEAU,
        "Плато Наковальни",
        planet=Planet.PYROXIS,
        layer=Layer.PLANET,
        parent=sphere,
        area=1,
    )
    port = await _ensure(
        session,
        PYROXIS_PORT,
        "Космодром на плато",
        planet=Planet.PYROXIS,
        layer=Layer.CITY,
        parent=plateau,
        area=PORT_AREA_M2,
        properties={"кольцо": 0},
    )
    await _yard(session, machine, port)


async def _aurora(session: AsyncSession, machine: Machine) -> None:
    sphere = await _sphere(session, "aurora")
    #: A world that has its cities already is left alone at once: six hundred
    #: key look-ups at every server start would be the price of idempotency
    #: paid in the wrong place.
    laid = await session.scalar(
        select(func.count())
        .select_from(Node)
        .where(Node.parent_id == sphere.id, Node.layer == Layer.PLANET)
    )
    if laid == AURORA_CITIES:
        return
    for number in range(1, AURORA_CITIES + 1):
        city = await _ensure(
            session,
            aurora_city_key(number),
            f"Заброшенный город №{number}",
            planet=Planet.AURORA,
            layer=Layer.PLANET,
            parent=sphere,
            area=1,
            properties={"предтечи": True},
        )
        port = await _ensure(
            session,
            aurora_port_key(number),
            f"Космодром города №{number}",
            planet=Planet.AURORA,
            layer=Layer.CITY,
            parent=city,
            area=PORT_AREA_M2,
            properties={"кольцо": 0, "предтечи": True},
        )
        await _relic_yard(session, port)


async def _sphere(session: AsyncSession, key: str) -> Node:
    """The planet's node on the space layer: `seed._system` lays it first."""
    return (await session.execute(select(Node).where(Node.key == key))).scalar_one()


async def _ensure(
    session: AsyncSession,
    key: str,
    name: str,
    *,
    planet: Planet,
    layer: Layer,
    parent: Node,
    area: float,
    properties: dict[str, object] | None = None,
) -> Node:
    """The node by key, created if the world has none yet."""
    found = (await session.execute(select(Node).where(Node.key == key))).scalar_one_or_none()
    if found is not None:
        return found
    return await world.create_node(
        session,
        key,
        name,
        planet=planet,
        area_m2=area,
        layer=layer,
        parent=parent,
        properties=dict(properties or {}),
    )


async def _yard(session: AsyncSession, machine: Machine, port: Node) -> None:
    """A spaceport is a node with a yard in it (D-206); one per node is enough."""
    if await world.has_station(session, port, ship.SPACEPORT):
        return
    await machine(session, port, _yard_name(), 60)


async def _relic_yard(session: AsyncSession, port: Node) -> None:
    """A yard the Forerunners left: granted with its provenance, not assembled."""
    if await world.has_station(session, port, ship.SPACEPORT):
        return
    await world.grant_item(
        session,
        await world.node_container(session, port),
        _yard_name(),
        quality=60,
        origin="наследие Предтеч: космодром заброшенного города Авроры",
    )


def _yard_name() -> str:
    """A concrete yard of the class: a world holds things, not classes (D-215)."""
    return current_catalog().recipes.of_class(ship.SPACEPORT)[0]
