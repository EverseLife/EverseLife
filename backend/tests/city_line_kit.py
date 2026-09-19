# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The city-line tests' shared fixtures (D-356): a town pinned on the sphere,
nodes laid by metres off its middle, the town's head at its hall. Used by
`test_city_line.py` and `test_races_city_line.py`; not collected by pytest.
"""

from __future__ import annotations

import math
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import globe
from src.constants import Catalog, Constants
from src.engine import city as town
from src.engine import places, world
from src.models.event import Event, EventKind
from src.models.identity import Body, Identity
from src.models.world import PLOT, Layer, Node, Planet

#: Where the test's town stands: well away from the origin every seated node
#: of the suite clusters round.
TOWN_LAT = 20.0
TOWN_LON = 40.0


def _at(constants: Constants, east_m: float, north_m: float) -> dict:
    """A place `east_m` and `north_m` metres off the town's middle."""
    per_deg = globe.radius_m(constants, Planet.TERRA) * math.pi / 180
    lat = TOWN_LAT + north_m / per_deg
    lon = TOWN_LON + east_m / (per_deg * math.cos(math.radians(TOWN_LAT)))
    return {places.PLACE: {places.PLACE_LAT: lat, places.PLACE_LON: lon}}


async def _node(
    session: AsyncSession,
    constants: Constants,
    name: str,
    east_m: float,
    north_m: float,
    *,
    parent: Node | None = None,
    properties: dict | None = None,
) -> Node:
    return await world.create_node(
        session,
        f"terra.line.{name}.{uuid.uuid4().hex[:8]}",
        "",
        area_m2=100,
        layer=Layer.PLANET,
        parent=parent,
        properties=_at(constants, east_m, north_m) | (properties or {}),
    )


async def _town(session: AsyncSession, constants: Constants, catalog: Catalog):
    """A town of four nodes twenty-five metres round its own: the frame.

    The city's node -- its administration stands there, where land is handed
    out (D-155) -- a location of its own east of it, and two plots north and
    west, all hanging on the city's node, as a founding lays them.
    """
    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    home = await _node(session, constants, "home", 0, 0)
    home.parent_id = sphere.id
    city = await town.found(session, catalog, home, f"Черта-{stamp}")
    gate = await _node(session, constants, "gate", 25, 0, parent=home)
    north = await _node(session, constants, "north", 0, 25, parent=home, properties={PLOT: True})
    west = await _node(session, constants, "west", -25, 0, parent=home, properties={PLOT: True})
    for node in (home, gate, north, west):
        node.owner_city_id = city.id
    yard = await world.node_container(session, home)
    await world.grant_item(session, yard, town.HALL, quality=65, origin="тест")
    await session.flush()
    return city, home, gate, north, west


async def _head(session: AsyncSession, city, home: Node) -> tuple[Identity, Body]:
    """The town's founder, standing at its administration: the `land` right."""
    identity = await world.create_identity(session, f"Глава-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, home)
    await town.install_founder(session, city, identity)
    return identity, body


async def _events(session: AsyncSession, kind: EventKind, node: Node) -> list[Event]:
    return list(
        (
            await session.execute(
                select(Event).where(Event.kind == kind.value, Event.node_id == node.id)
            )
        )
        .scalars()
        .all()
    )
