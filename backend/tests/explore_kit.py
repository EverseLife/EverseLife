# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Exploration's shared helpers: where the capital is pinned, a sphere, a
camp with a scout on it, a step over the globe and the band a node is judged
by. Used by the exploration files (`test_explore*.py`); not collected by
pytest.
"""

from __future__ import annotations

import json
import math
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from conftest import VAULT_BUILD
from src import globe
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import biome, explore, facet, places, world
from src.models.identity import Body
from src.models.world import Layer, Node, Planet


def _capital() -> tuple[float, float]:
    """Where the layout pins the capital: read off the build the tests run
    on, because the field decides where land is and the pin follows it
    (landscape plan, wave 2).

    Asked by the mark and not by the key: the capital is the first node the
    layout founds a city on, and which node that is has moved once already --
    D-330 put the city on its bioprinter and took away the empty node above
    it, and a test that knew the old key stopped collecting at import.
    """
    layout = json.loads((VAULT_BUILD / "world.json").read_text(encoding="utf-8"))
    nodes = layout["nodes"] if isinstance(layout, dict) else layout
    place = next(
        node["place"]
        for node in nodes
        if node.get("city") and (node.get("place") or {}).get("lat") is not None
    )
    return float(place["lat"]), float(place["lon"])


CAPITAL = _capital()


async def _sphere(session: AsyncSession, planet: Planet = Planet.TERRA) -> Node:
    return await world.create_node(
        session, planet.value, planet.value.title(), area_m2=1, planet=planet, layer=Layer.SPACE
    )


def _pin(point: globe.Geo) -> dict:
    return {places.PLACE: {places.PLACE_LAT: point[0], places.PLACE_LON: point[1]}}


async def _camp(
    session: AsyncSession,
    constants: Constants,
    planet: Planet = Planet.TERRA,
    at: globe.Geo = CAPITAL,
    area: float = 60,
) -> tuple[Node, Node, Body]:
    """A sphere, a seeded node on it and a scout standing there. `area` is
    the node's land: the far reach is counted past its edge."""
    sphere = await _sphere(session, planet)
    point = explore.point_of(constants, planet, explore.cell_of(constants, planet, at))
    camp = await world.create_node(
        session,
        f"{planet.value}.camp.{uuid.uuid4().hex[:6]}",
        "Camp",
        planet=planet,
        area_m2=area,
        parent=sphere,
        properties=_pin(point),
    )
    identity = await world.create_identity(session, f"Scout-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, camp)
    body.stamina = constants[R.BODY_STAMINA_MAX]
    await session.flush()
    return sphere, camp, body


def _step(
    constants: Constants, planet: Planet, origin: globe.Geo, metres: float, bearing: float = 0.0
) -> globe.Geo:
    radius = globe.radius_m(constants, planet)
    return globe.offset(radius, origin, metres * math.sin(bearing), metres * math.cos(bearing))


def _reach(constants: Constants, catalog: Catalog, node: Node) -> tuple[float, float]:
    """The band `explore.check` will measure this node's aim against.

    The **facet's** band from the node's centre, its own land added
    (`facet.band_m`), not the biome's. They are not the same: a face may
    narrow the biome's reach by as much as the vault's reeds do, and asking
    the biome alone gave a step the engine then refused for being too far.
    It held only while the camp happened to stand on a face that narrows
    little, and every rebuilt field moves the camp -- D-324 moved it once,
    D-329 again, and the second time it broke here (38 m against a 25 m
    reach). A test that measures by one rule what the engine judges by
    another is a trap that re-arms itself on the next world.
    """
    here = biome.of_node(constants, node)
    assert here is not None
    return facet.band_m(constants, catalog, node, here)
