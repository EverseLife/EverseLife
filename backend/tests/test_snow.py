# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The season's snow in the engine, and the way through it (D-338).

Checked is what the decision is taken for:

* the engine has the map's law of snow: white under the snow line, a dry
  cold keeping a share of it, none on the water, none off the sphere, and
  Aurora under snow all year;
* the snow lengthens the off-road -- the wild and the trail -- and never a
  road, on the departure, in the exits and in the route, which goes round
  by the road when the snow makes the short cut the long way;
* the scout's run is a walk over the wild and pays the snow too;
* Aurora's finds written as ice before its ice drew back to the poles are
  read again, and the polar ones stay ice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src import seed_catchup
from src.constants import Constants
from src.constants import registry as R
from src.engine import biome, climate, facet, places, terrain, travel, world
from src.engine.explore import run
from src.models.world import Edge, Node, Planet, Surface

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _land(constants: Constants, planet: Planet, test) -> tuple[float, float]:
    field = terrain.field_of(constants, planet)
    return next(
        (float(lat), float(lon))
        for lat in range(-86, 87, 2)
        for lon in range(-180, 180, 3)
        if not field.is_water(lat, lon) and test(field, float(lat), float(lon))
    )


def _year(constants: Constants, planet: Planet) -> list[datetime]:
    period = climate.sky.circle_of(constants, planet.value)[1]
    return [EPOCH + timedelta(days=period * step / 24) for step in range(24)]


# --- the law -------------------------------------------------------------------


def test_the_snow_lies_where_the_map_draws_it(constants: Constants) -> None:
    line = constants[R.SEASON_SNOW_C]
    band = constants[R.SEASON_SNOW_BAND_C]
    keep = constants[R.SEASON_SNOW_DRY_SHARE] / 100
    dry_rain = constants[R.SEASON_SNOW_DRY_RAIN] / 100

    #: Hot ground never sees it, at any season.
    hot = _land(constants, Planet.TERRA, lambda f, lat, lon: f.temperature_at(lat, lon) > line + 25)
    assert all(
        climate.snow_now(constants, Planet.TERRA, *hot, EPOCH, t) == 0
        for t in _year(constants, Planet.TERRA)
    )

    #: Wet ground far under the line is white through; dry ground keeps the
    #: dry share of it.
    def frozen(wet: bool):
        def test(f, lat, lon) -> bool:
            deep = (
                f.temperature_at(lat, lon)
                + abs(climate.season_c(constants, Planet.TERRA, 90.0, EPOCH, EPOCH))
                < line - band - 40
            )
            rain = f.rain_at(lat, lon)
            return deep and (rain >= dry_rain if wet else rain == 0)

        return test

    wet = _land(constants, Planet.AURORA, frozen(True))
    assert climate.snow_now(constants, Planet.AURORA, *wet, EPOCH, EPOCH) == pytest.approx(1.0)
    #: A dry cold keeps the dry share: read where the rain share is nought,
    #: or -- a planet with no such place -- by the rain read as nought there.
    dry = _land(constants, Planet.AURORA, frozen(True))
    ground = terrain.field_of(constants, Planet.AURORA)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(type(ground), "rain_at", lambda self, lat, lon: 0.0)
        climate._HOURS.clear()
        assert climate.snow_now(constants, Planet.AURORA, *dry, EPOCH, EPOCH) == pytest.approx(keep)
    climate._HOURS.clear()

    #: Aurora is under snow all the year round, and none lies on its water.
    for moment in _year(constants, Planet.AURORA):
        assert climate.snow_now(constants, Planet.AURORA, *wet, EPOCH, moment) > 0
    field = terrain.field_of(constants, Planet.AURORA)
    sea = next(
        (float(lat), float(lon))
        for lat in range(-40, 41, 5)
        for lon in range(-180, 180, 5)
        if field.is_water(lat, lon)
    )
    assert climate.snow_now(constants, Planet.AURORA, *sea, EPOCH, EPOCH) == 0

    #: Off the sphere there is a floor, not a ground.
    room = Node(key="aurora.room", name="room", planet=Planet.AURORA, properties={})
    assert climate.snow_on(constants, room, EPOCH, EPOCH) == 0


def test_the_season_brings_the_snow_and_takes_it(constants: Constants) -> None:
    """Somewhere on Terra the snow comes in winter and goes in summer (D-334)."""
    line = constants[R.SEASON_SNOW_C]
    year = _year(constants, Planet.TERRA)
    swing = constants[R.SEASON_SWING_C]["terra"]
    point = _land(
        constants,
        Planet.TERRA,
        lambda f, lat, lon: (
            abs(lat) > 40
            and abs(f.temperature_at(lat, lon) - line) < swing / 3
            and f.rain_at(lat, lon) > constants[R.SEASON_SNOW_DRY_RAIN] / 100
        ),
    )
    depths = [climate.snow_now(constants, Planet.TERRA, *point, EPOCH, t) for t in year]
    assert min(depths) == 0 and max(depths) > 0.9


# --- the way ---------------------------------------------------------------------


def test_the_snow_lengthens_the_off_road_and_not_the_road(constants: Constants) -> None:
    times = constants[R.TRAVEL_SNOW_MULTIPLIER]
    for surface in Surface:
        edge = Edge(base_seconds=100, surface=surface)
        bare = travel.edge_seconds(constants, edge, snow=0.0)
        white = travel.edge_seconds(constants, edge, snow=1.0)
        half = travel.edge_seconds(constants, edge, snow=0.5)
        if surface in travel.OFF_ROAD:
            assert white == pytest.approx(bare * times)
            assert half == pytest.approx(bare * (1 + (times - 1) / 2))
        else:
            assert white == bare == half
    assert {Surface.WILD, Surface.TRAIL} == travel.OFF_ROAD


async def _walker(session: AsyncSession, here: Node):
    stamp = uuid.uuid4().hex[:8]
    identity = await world.create_identity(session, f"walker-{stamp}")
    return await world.print_body(session, identity, here)


async def _node(session: AsyncSession, name: str) -> Node:
    return await world.create_node(
        session, f"terra.{name}.{uuid.uuid4().hex[:8]}", name, area_m2=100
    )


async def test_a_leg_through_the_snow_is_longer(
    session: AsyncSession, constants: Constants, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The departure pays the snow: the leg's time and the strength it costs."""
    here, there = await _node(session, "here"), await _node(session, "there")
    wild = await travel.connect(session, here, there, base_seconds=600, surface=Surface.WILD)
    body = await _walker(session, here)
    body.stamina = Decimal(100)
    moment = datetime.now(UTC)
    monkeypatch.setattr(climate, "snow_now", lambda *_args: 1.0)
    going = await travel.depart(session, constants, body, there, now=moment)
    seconds = (going.arrives_at - moment).total_seconds()
    times = constants[R.TRAVEL_SNOW_MULTIPLIER]
    assert seconds == pytest.approx(travel.edge_seconds(constants, wild, snow=0.0) * times, abs=1)
    spent = 100 - float(body.stamina)
    assert spent == pytest.approx(
        travel.stamina_cost(constants, seconds, transport=False), rel=0.02
    ), "the strength follows the longer leg"


async def test_the_exits_and_the_route_know_the_snow(
    session: AsyncSession, constants: Constants, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A short cut over the wild is the fast way in summer and the slow one
    in winter: the route goes round by the road, and the exits say why."""
    a, b, c = await _node(session, "a"), await _node(session, "b"), await _node(session, "c")
    wild = constants[R.ROAD_WILD_MULTIPLIER]
    road = constants[R.ROAD_ROAD_MULTIPLIER]
    times = constants[R.TRAVEL_SNOW_MULTIPLIER]
    #: The short cut is quicker bare and slower white: base x wild against
    #: the road's two legs, and the snow's factor between them.
    around = 150.0
    cut = 2 * around * road / wild * (1 + times) / (2 * times)
    assert cut * wild < 2 * around * road < cut * wild * times
    await travel.connect(session, a, b, base_seconds=cut, surface=Surface.WILD)
    await travel.connect(session, a, c, base_seconds=around, surface=Surface.ROAD)
    await travel.connect(session, c, b, base_seconds=around, surface=Surface.ROAD)

    monkeypatch.setattr(climate, "snow_now", lambda *_args: 0.0)
    assert await travel.route(session, constants, a.id, b.id) == [b.id]
    bare = {exit.node_id: exit.seconds for exit in await travel.exits(session, constants, a)}

    monkeypatch.setattr(climate, "snow_now", lambda *_args: 1.0)
    assert await travel.route(session, constants, a.id, b.id) == [c.id, b.id]
    white = {exit.node_id: exit.seconds for exit in await travel.exits(session, constants, a)}
    assert white[b.id] == pytest.approx(bare[b.id] * times)
    assert white[c.id] == pytest.approx(bare[c.id]), "the road knows no snow"


def test_the_scouts_run_pays_the_snow(constants: Constants) -> None:
    metres = 200.0
    bare = run._wild_seconds(constants, metres, 0.0)
    assert run._wild_seconds(constants, metres, 1.0) == pytest.approx(
        bare * constants[R.TRAVEL_SNOW_MULTIPLIER]
    )


# --- Aurora's finds ----------------------------------------------------------------


async def test_auroras_ice_finds_are_read_again_under_snow(
    session: AsyncSession, constants: Constants
) -> None:
    field = terrain.field_of(constants, Planet.AURORA)
    snow = _land(constants, Planet.AURORA, lambda f, lat, lon: not f.ice_at(lat, lon))
    cap = _land(constants, Planet.AURORA, lambda f, lat, lon: bool(f.ice_at(lat, lon)))
    assert field.ice_at(*cap) and not field.ice_at(*snow)

    async def find(point: tuple[float, float]) -> Node:
        return await world.create_node(
            session,
            f"aurora.find.{uuid.uuid4().hex[:8]}",
            "",
            planet=Planet.AURORA,
            area_m2=100,
            properties={
                biome.BIOME: biome.ICE,
                places.PLACE: {places.PLACE_LAT: point[0], places.PLACE_LON: point[1]},
            },
        )

    open_find, polar_find = await find(snow), await find(cap)
    await seed_catchup._aurora_under_snow(session, constants)
    assert open_find.properties[biome.BIOME] == biome.SNOW
    assert open_find.properties[facet.FACET], "the find wears a snow field's face"
    assert polar_find.properties[biome.BIOME] == biome.ICE
    #: A second run finds nothing left to change.
    await seed_catchup._aurora_under_snow(session, constants)
    assert open_find.properties[biome.BIOME] == biome.SNOW
