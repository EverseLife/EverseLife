# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Where a node stands, once and for everybody (D-237, D-319).

A surface node stands on its planet's sphere in degrees, one level for the
whole planet; a room stands on the flat plan of its house in map units. What
is checked here is the whole of the rule:

* a new node is a step from what it was laid beside, measured in metres on
  the sphere, and never nearer than the gap to anybody;
* a place given once is never recomputed, however the map grows around it;
* a crowd round one anchor spreads over rings instead of piling up;
* the seed's pin is taken as given, and a seat is searched only for the rest;
* the sky keeps no places, and the inside keeps flat ones -- round the
  house's ground floor, which holds the origin of its plan.
"""

from __future__ import annotations

import math
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src import globe
from src.constants import Constants
from src.constants import registry as R
from src.engine import places, props, world
from src.models.world import STOREY, Layer, Node, Planet
from src.runtime import MAP_MIN_GAP, MAP_STEP


async def _node(
    session: AsyncSession,
    name: str,
    *,
    layer: Layer = Layer.PLANET,
    parent: Node | None = None,
    anchor: Node | None = None,
    planet: Planet = Planet.TERRA,
) -> Node:
    return await world.create_node(
        session,
        f"place.{uuid.uuid4().hex}",
        name,
        area_m2=100,
        layer=layer,
        parent=parent,
        anchor=anchor,
        planet=planet,
    )


def _metres(constants: Constants, one: Node, other: Node) -> float:
    gap = places.distance_m(constants, one, other)
    assert gap is not None
    return gap


async def test_a_node_stands_a_step_from_what_it_was_laid_from(
    session: AsyncSession, constants: Constants
) -> None:
    """The step is metres on the sphere, and the same for everybody (D-237, D-319)."""
    terra = await _node(session, "Терра", layer=Layer.SPACE)
    city = await _node(session, "Город", parent=terra)
    core = await _node(session, "Ядро", parent=city)
    library = await _node(session, "library", parent=city, anchor=core)
    step = constants[R.MAP_CITY_STEP_M]
    assert places.geo_of(city) == places.ORIGIN_GEO, "первый узел поверхности — её начало"
    assert _metres(constants, city, core) == pytest.approx(step, rel=1e-3)
    assert _metres(constants, core, library) == pytest.approx(step, rel=1e-3)
    assert places.place_of(core) is None, "у узла поверхности нет плоского места"


async def test_nobody_moves_when_the_map_grows(session: AsyncSession) -> None:
    """A place given once is never recomputed: the world has no wipes (D-007)."""
    terra = await _node(session, "Терра", layer=Layer.SPACE)
    city = await _node(session, "Город", parent=terra)
    core = await _node(session, "Ядро", parent=city)
    library = await _node(session, "library", parent=city, anchor=core)
    was = places.geo_of(library)
    for number in range(5):
        await _node(session, f"Дом {number}", parent=city, anchor=core)
    assert places.geo_of(library) == was, "соседи появились — библиотека не сдвинулась"
    await places.assign(session, library, anchor=core)
    assert places.geo_of(library) == was


async def test_nodes_never_sit_on_one_another(session: AsyncSession, constants: Constants) -> None:
    """A crowd around one node spreads instead of piling up."""
    terra = await _node(session, "Терра", layer=Layer.SPACE)
    city = await _node(session, "Город", parent=terra)
    core = await _node(session, "Ядро", parent=city)
    houses = [await _node(session, f"Дом {n}", parent=city, anchor=core) for n in range(12)]
    gap = constants[R.MAP_MIN_GAP_M]
    for i, one in enumerate(houses):
        for other in houses[i + 1 :]:
            assert _metres(constants, one, other) >= gap * (1 - 1e-6), (
                "две точки легли друг на друга"
            )


async def test_a_crowd_past_the_old_ceiling_still_spreads(
    session: AsyncSession, constants: Constants
) -> None:
    """Enough nodes round one anchor to fill six rings, and not one pair on top
    of another: the walk out widens until there is room, and never keeps a
    spot it found taken (D-007)."""
    terra = await _node(session, "Терра", layer=Layer.SPACE)
    city = await _node(session, "Город", parent=terra)
    crowd = [await _node(session, f"Дом {n}", parent=terra, anchor=city) for n in range(240)]
    points = [places.geo_of(node) for node in crowd] + [places.geo_of(city)]
    assert all(point is not None for point in points)
    radius = globe.radius_m(constants, Planet.TERRA)
    gap = constants[R.MAP_MIN_GAP_M]
    for i, one in enumerate(points):
        for other in points[i + 1 :]:
            assert globe.distance_m(radius, one, other) >= gap * (1 - 1e-6), (
                "две точки легли друг на друга"
            )


async def test_a_find_stands_next_to_where_it_was_made_from(
    session: AsyncSession, constants: Constants
) -> None:
    """One surface level (D-319): a node laid from inside a city stands beside
    the very node it was laid from, not beside the city's point."""
    terra = await _node(session, "Терра", layer=Layer.SPACE)
    city = await _node(session, "Столица", parent=terra)
    edge = await _node(session, "Край города", parent=city)
    field = await _node(session, "Поле", parent=terra, anchor=edge)
    step = constants[R.MAP_CITY_STEP_M]
    assert _metres(constants, edge, field) == pytest.approx(step, rel=1e-3)


async def test_the_pin_is_taken_as_given(session: AsyncSession, constants: Constants) -> None:
    """The seed names where a city stands; the seat is searched only for the rest."""
    terra = await _node(session, "Терра", layer=Layer.SPACE)
    city = Node(
        key=f"place.{uuid.uuid4().hex}",
        name="Столица",
        planet=Planet.TERRA,
        layer=Layer.PLANET,
        parent_id=terra.id,
        area_m2=1,
        properties={},
    )
    session.add(city)
    await session.flush()
    await places.pin(session, city, (41.0, 24.0))
    assert places.geo_of(city) == (41.0, 24.0)
    with pytest.raises(places.PlaceIsFixed):
        await places.pin(session, city, (0.0, 0.0))
    core = await _node(session, "Ядро", parent=city)
    assert _metres(constants, city, core) == pytest.approx(constants[R.MAP_CITY_STEP_M], rel=1e-3)
    #: The wire tells the client degrees for the surface, and nothing else.
    assert places.wire(city) == {"lat": 41.0, "lon": 24.0}


async def test_the_pole_is_never_a_seat(session: AsyncSession, constants: Constants) -> None:
    """A step north of the last latitude stays on this side of the pole."""
    radius = globe.radius_m(constants, Planet.TERRA)
    lat, _ = globe.offset(radius, (globe.LAST_LAT, 10.0), 0.0, 100_000.0)
    assert lat == globe.LAST_LAT
    _, lon = globe.offset(radius, (0.0, 179.9), 100_000.0, 0.0)
    assert -180.0 <= lon < 180.0, "долгота замыкается, а не уходит за 180"


async def test_two_planets_share_no_ground(session: AsyncSession, constants: Constants) -> None:
    """Distance is a question about one sphere: across two there is none."""
    terra = await _node(session, "Терра", layer=Layer.SPACE)
    pyroxis = await _node(session, "Пироксис", layer=Layer.SPACE, planet=Planet.PYROXIS)
    home = await _node(session, "Дом", parent=terra)
    field = await _node(session, "Поле", parent=pyroxis, planet=Planet.PYROXIS)
    assert places.distance_m(constants, home, field) is None
    assert places.distance_m(constants, home, terra) is None


async def test_the_sky_keeps_no_places(session: AsyncSession) -> None:
    """A planet's point is a function of the clock, and a stored one would argue with it."""
    terra = await _node(session, "Терра", layer=Layer.SPACE)
    assert places.geo_of(terra) is None
    assert places.place_of(terra) is None
    assert places.wire(terra) is None


def _apart(one: tuple[float, float] | None, other: tuple[float, float] | None) -> float:
    assert one is not None and other is not None
    return math.hypot(one[0] - other[0], one[1] - other[1])


async def test_the_inside_is_flat(session: AsyncSession) -> None:
    """Floors and rooms keep their flat plan: no north, no degrees, the old step.

    A hull's rooms: the ship is a point of the sky, there is no floor under
    them, and the first room takes the origin of the plan.
    """
    terra = await _node(session, "Терра", layer=Layer.SPACE)
    hull = await _node(session, "Корпус", layer=Layer.SPACE, parent=terra)
    first = await _node(session, "Рубка", layer=Layer.LOCATION, parent=hull)
    second = await _node(session, "Трюм", layer=Layer.LOCATION, parent=hull, anchor=first)
    assert places.geo_of(first) is None
    assert places.place_of(first) == places.ORIGIN
    there = places.place_of(second)
    assert _apart(places.place_of(first), there) == pytest.approx(MAP_STEP)
    assert MAP_STEP >= MAP_MIN_GAP
    assert there is not None
    assert places.wire(second) == {"x": there[0], "y": there[1]}


async def test_a_house_keeps_the_origin_for_its_ground_floor(session: AsyncSession) -> None:
    """The plot is the ground floor (D-247), drawn at the origin of its floors' plan.

    So the floors above are seated round it, never on it: the second floor
    used to take the origin -- the first node of its group, laid next to a plot
    with no flat place -- and on the map it covered the floor below, which
    could then not be picked from upstairs (owner, 2026-09-19).
    """
    terra = await _node(session, "Терра", layer=Layer.SPACE)
    plot = await _node(session, "Участок", parent=terra)
    second = await _node(session, "2-й этаж", layer=Layer.LOCATION, parent=plot, anchor=plot)
    third = await _node(session, "3-й этаж", layer=Layer.LOCATION, parent=plot, anchor=second)
    assert _apart(places.place_of(second), places.ORIGIN) == pytest.approx(MAP_STEP)
    assert _apart(places.place_of(third), places.place_of(second)) == pytest.approx(MAP_STEP)
    assert _apart(places.place_of(third), places.ORIGIN) >= MAP_MIN_GAP


async def test_a_floor_laid_on_its_ground_floor_is_seated_off_it(session: AsyncSession) -> None:
    """The repair: a floor at the origin moves once, and nothing else does."""
    terra = await _node(session, "Терра", layer=Layer.SPACE)
    plot = await _node(session, "Участок", parent=terra)
    second = await _node(session, "2-й этаж", layer=Layer.LOCATION, parent=plot, anchor=plot)
    third = await _node(session, "3-й этаж", layer=Layer.LOCATION, parent=plot, anchor=second)
    hull = await _node(session, "Корпус", layer=Layer.SPACE, parent=terra)
    cabin = await _node(session, "Рубка", layer=Layer.LOCATION, parent=hull)
    #: The house as the old rule laid it: the second floor on the origin, the
    #: third a step from it.
    for floor, storey, spot in ((second, 2, (0.0, 0.0)), (third, 3, (MAP_STEP, 0.0))):
        await props.stamp(
            session,
            floor,
            {
                STOREY: storey,
                places.PLACE: {places.PLACE_X: spot[0], places.PLACE_Y: spot[1]},
            },
        )
    kept = places.place_of(third)
    #: A house of two storeys: nothing above, so wherever a new floor would go.
    low = await _node(session, "Участок", parent=terra)
    only = await _node(session, "2-й этаж", layer=Layer.LOCATION, parent=low, anchor=low)
    await props.stamp(
        session, only, {STOREY: 2, places.PLACE: {places.PLACE_X: 0.0, places.PLACE_Y: 0.0}}
    )

    assert await places.floors_off_the_ground(session) == 2
    moved = places.place_of(second)
    #: A step from both its neighbours: the stairs down and up stay short.
    assert _apart(moved, places.ORIGIN) == pytest.approx(MAP_STEP)
    assert _apart(moved, kept) == pytest.approx(MAP_STEP)
    assert places.place_of(third) == kept
    assert _apart(places.place_of(only), places.ORIGIN) == pytest.approx(MAP_STEP)
    #: A hull's first room stands on the origin by right: there is no floor under it.
    assert places.place_of(cabin) == places.ORIGIN
    #: And a second run finds nothing to move.
    assert await places.floors_off_the_ground(session) == 0
    assert places.place_of(second) == moved
