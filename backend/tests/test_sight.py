# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The map is what one sees, remembers and is told (D-240, D-319).

Three sources and two tones: the eye reaches `map.sight_km` over the globe
and one step of the graph; the memory of places is drawn dark; the cities of
the planet are public and dark unless in sight. The sky is everybody's and
carries no way in; whatever is drawn brings its parents; a reader with no
body gets the sky.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src import globe
from src.api.routes.public import _standing
from src.constants import Constants
from src.constants import registry as R
from src.engine import account as accounts
from src.engine import places, sight, travel, world
from src.models.identity import Account
from src.models.world import Layer, Node, Planet, Surface
from src.units import METRES_PER_KM

HOME = (41.0, 24.0)


async def _sphere(session: AsyncSession, planet: Planet) -> Node:
    return await world.create_node(
        session, planet.value, planet.value.title(), area_m2=1, planet=planet, layer=Layer.SPACE
    )


def _pin(point: globe.Geo) -> dict:
    return {places.PLACE: {places.PLACE_LAT: point[0], places.PLACE_LON: point[1]}}


async def _node(
    session: AsyncSession,
    key: str,
    parent: Node | None,
    *,
    at: globe.Geo | None = None,
    planet=Planet.TERRA,
    layer=Layer.PLANET,
) -> Node:
    return await world.create_node(
        session,
        f"{key}.{uuid.uuid4().hex[:6]}",
        key,
        area_m2=100,
        planet=planet,
        layer=layer,
        parent=parent,
        properties=_pin(at) if at is not None else None,
    )


def _away(constants: Constants, km: float, bearing: float = 0.0) -> globe.Geo:
    radius = globe.radius_m(constants, Planet.TERRA)
    import math

    return globe.offset(
        radius, HOME, km * METRES_PER_KM * math.sin(bearing), km * METRES_PER_KM * math.cos(bearing)
    )


async def _graph(session: AsyncSession) -> tuple[list[Node], list]:
    return await sight.read(session)


async def test_the_eye_reaches_the_sight_radius_and_no_farther(
    session: AsyncSession, constants: Constants
) -> None:
    """A distance on the globe, not a count of steps (D-319 п. 6)."""
    terra = await _sphere(session, Planet.TERRA)
    reach = constants[R.MAP_SIGHT_KM]
    home = await _node(session, "terra.home", terra, at=HOME)
    near = await _node(session, "terra.near", terra, at=_away(constants, reach * 0.5))
    far = await _node(session, "terra.far", terra, at=_away(constants, reach * 2))
    nodes, edges = await _graph(session)
    view = sight.around(home, constants=constants, nodes=nodes, edges=edges)
    assert home.id in view.seen and near.id in view.seen
    assert far.id not in view.seen, "за радиусом взгляда узел не рисуется"
    assert near.id not in view.faded, "в радиусе — ярко"


async def test_a_step_of_the_graph_is_seen_whatever_the_distance(
    session: AsyncSession, constants: Constants
) -> None:
    """The gangway, the corridor, the door: one step is always in sight."""
    terra = await _sphere(session, Planet.TERRA)
    home = await _node(session, "terra.home", terra, at=HOME)
    far = await _node(
        session, "terra.far", terra, at=_away(constants, constants[R.MAP_SIGHT_KM] * 3)
    )
    room = await _node(session, "terra.home.room", home, layer=Layer.LOCATION)
    await travel.connect(session, home, far, base_seconds=60, surface=Surface.ROAD)
    await travel.connect(session, home, room, base_seconds=1, surface=Surface.PAVED)
    nodes, edges = await _graph(session)
    view = sight.around(home, constants=constants, nodes=nodes, edges=edges)
    assert far.id in view.seen and room.id in view.seen
    #: Standing in the room, the eye looks out from the house it is in.
    view = sight.around(room, constants=constants, nodes=nodes, edges=edges)
    assert home.id in view.seen


async def test_memory_and_the_public_are_drawn_dark(
    session: AsyncSession, constants: Constants
) -> None:
    """A remembered place beyond the eye is there, dark; a city of the planet
    too; the same node in sight is bright."""
    terra = await _sphere(session, Planet.TERRA)
    reach = constants[R.MAP_SIGHT_KM]
    home = await _node(session, "terra.home", terra, at=HOME)
    remembered = await _node(session, "terra.remembered", terra, at=_away(constants, reach * 3))
    forgotten = await _node(
        session, "terra.forgotten", terra, at=_away(constants, reach * 3, bearing=1.0)
    )
    city = await _node(session, "terra.city", terra, at=_away(constants, reach * 4, bearing=2.0))
    plot = await _node(
        session, "terra.city.plot", city, at=_away(constants, reach * 4, bearing=2.0)
    )
    nodes, edges = await _graph(session)
    view = sight.around(home, constants=constants, nodes=nodes, edges=edges, known={remembered.key})
    assert remembered.id in view.seen and remembered.id in view.faded, "память темна"
    assert forgotten.id not in view.seen, "что не помнишь и не видишь — не рисуется"
    assert city.id in view.seen and plot.id in view.seen, "город публичен (D-097)"
    assert city.id in view.faded and plot.id in view.faded
    assert home.id not in view.faded and terra.id not in view.faded


async def test_another_planet_has_no_surface_to_expand(
    session: AsyncSession, constants: Constants
) -> None:
    """The sphere is drawn; what is on it is not in the answer at all -- not
    even a city of it, and not even a remembered place of it."""
    terra = await _sphere(session, Planet.TERRA)
    pyroxis = await _sphere(session, Planet.PYROXIS)
    home = await _node(session, "terra.home", terra, at=HOME)
    plateau = await _node(session, "pyroxis.anvil", pyroxis, at=(0.0, 0.0), planet=Planet.PYROXIS)
    nodes, edges = await _graph(session)
    view = sight.around(home, constants=constants, nodes=nodes, edges=edges, known={plateau.key})
    assert pyroxis.id in view.seen, "планета в небе видна всем: это арифметика орбит"
    assert plateau.id not in view.seen, "а её поверхность — нет: туда надо долететь"


async def test_a_reader_with_no_body_gets_the_sky(
    session: AsyncSession, constants: Constants
) -> None:
    terra = await _sphere(session, Planet.TERRA)
    home = await _node(session, "terra.home", terra, at=HOME)
    nodes, edges = await _graph(session)
    view = sight.around(None, constants=constants, nodes=nodes, edges=edges)
    assert terra.id in view.seen and home.id not in view.seen and not view.faded


async def test_a_token_names_the_body_and_rubbish_names_nobody(session: AsyncSession) -> None:
    """The header is optional, and a stale tab gets a distant map, not an error."""
    terra = await _sphere(session, Planet.TERRA)
    home = await _node(session, "terra.home", terra, at=HOME)
    identity = await world.create_identity(session, f"Walker-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, home)
    account = await session.get(Account, identity.account_id)
    token = await accounts.issue_token(session, account)
    assert (await _standing(session, f"Bearer {token}")).node_id == body.node_id
    assert await _standing(session, "Bearer nonsense") is None
    assert await _standing(session, None) is None
    assert await _standing(session, token) is None, "без схемы это не заголовок"
