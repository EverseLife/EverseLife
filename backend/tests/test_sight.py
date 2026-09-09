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

import math
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src import globe
from src.api.routes.public import _standing
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import account as accounts
from src.engine import mapshot, places, sight, travel, world
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


async def test_a_step_into_an_inside_is_seen_and_a_road_neighbour_is_not(
    session: AsyncSession, constants: Constants
) -> None:
    """The gangway, the corridor, the door: the step into an inside is in
    sight; a road neighbour beyond the eye is not, for being joined."""
    terra = await _sphere(session, Planet.TERRA)
    home = await _node(session, "terra.home", terra, at=HOME)
    far = await _node(
        session, "terra.far", terra, at=_away(constants, constants[R.MAP_SIGHT_KM] * 3)
    )
    room = await _node(session, "terra.home.room", home, layer=Layer.LOCATION)
    await travel.connect(session, home, far, base_seconds=60, surface=Surface.WILD)
    await travel.connect(session, home, room, base_seconds=1, surface=Surface.PAVED)
    nodes, edges = await _graph(session)
    view = sight.around(home, constants=constants, nodes=nodes, edges=edges)
    assert room.id in view.seen and far.id not in view.seen
    #: Standing in the room, the eye looks out from the house it is in.
    view = sight.around(room, constants=constants, nodes=nodes, edges=edges)
    assert home.id in view.seen


async def test_memory_and_the_public_are_drawn_dark(
    session: AsyncSession, constants: Constants
) -> None:
    """A remembered place beyond the eye is there, dark; a city of the planet
    too; a frozen city of the Forerunners is not public; in sight all is bright."""
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
    #: A frozen city of the Forerunners hangs its rooms on itself like a city
    #: does, and is nobody's polity: a find, known by sight and memory alone.
    ruin = await _node(session, "terra.lost", terra, at=_away(constants, reach * 5, bearing=3.0))
    hall = await _node(
        session, "terra.lost.hall", ruin, at=_away(constants, reach * 5, bearing=3.0)
    )
    nodes, edges = await _graph(session)
    view = sight.around(
        home,
        constants=constants,
        nodes=nodes,
        edges=edges,
        known={remembered.key},
        cities={city.id},
    )
    assert remembered.id in view.seen and remembered.id in view.faded, "память темна"
    assert forgotten.id not in view.seen, "что не помнишь и не видишь — не рисуется"
    assert city.id in view.seen and plot.id in view.seen, "город публичен (D-097)"
    assert city.id in view.faded and plot.id in view.faded
    assert home.id not in view.faded and terra.id not in view.faded
    assert ruin.id not in view.seen and hall.id not in view.seen, "руины не публичны"
    #: A laid road is public (D-097): the node it reaches is drawn dark.
    roadside = await _node(
        session, "terra.roadside", terra, at=_away(constants, reach * 6, bearing=4.0)
    )
    wild = await _node(session, "terra.wild", terra, at=_away(constants, reach * 6, bearing=4.2))
    await travel.connect(session, city, roadside, base_seconds=600, surface=Surface.ROAD)
    await travel.connect(session, roadside, wild, base_seconds=600, surface=Surface.WILD)
    nodes, edges = await _graph(session)
    view = sight.around(home, constants=constants, nodes=nodes, edges=edges, cities={city.id})
    assert roadside.id in view.faded, "дорога публична"
    assert wild.id not in view.seen, "бездорожье за ней — нет"


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


async def test_an_edge_out_of_sight_is_a_stub_that_tells_the_way_and_not_the_end(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The map draws a way into the fog from its seen end (D-319 п. 6): the
    bearing to set out on, and neither how far nor where it ends."""
    terra = await _sphere(session, Planet.TERRA)
    reach = constants[R.MAP_SIGHT_KM]
    home = await _node(session, "terra.home", terra, at=HOME)
    near = await _node(session, "terra.near", terra, at=_away(constants, reach * 0.5))
    north = await _node(session, "terra.north", terra, at=_away(constants, reach * 3))
    east = await _node(session, "terra.east", terra, at=_away(constants, reach * 3, math.pi / 2))
    await travel.connect(session, home, near, base_seconds=60, surface=Surface.WILD)
    await travel.connect(session, home, north, base_seconds=600, surface=Surface.WILD)
    await travel.connect(session, near, east, base_seconds=600, surface=Surface.TRAIL)
    identity = await world.create_identity(session, f"Walker-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, home)
    answer = await mapshot.personal(session, constants, catalog, body, datetime.now(UTC))
    assert {row["key"] for row in answer["nodes"]} >= {home.key, near.key}
    assert north.key not in {row["key"] for row in answer["nodes"]}
    assert [(e["a"], e["b"]) for e in answer["edges"]] == [(home.key, near.key)]
    stubs = {row["from"]: row for row in answer["stubs"]}
    assert stubs.keys() == {home.key, near.key}, "по обрубку с каждого видимого конца"
    assert stubs[home.key]["bearing"] == 0 and stubs[home.key]["surface"] == "wild"
    #: East of home is east and a little south of a node north of home.
    assert globe.QUARTER_TURN < stubs[near.key]["bearing"] < globe.QUARTER_TURN + 15
    assert set(stubs[home.key]) == {"from", "bearing", "surface"}, "ни конца, ни расстояния"
