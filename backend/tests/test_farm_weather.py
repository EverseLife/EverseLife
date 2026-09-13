# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The weather and the ice act on the ground (D-338).

Checked is what the decision is taken for:

* the rain of the weather waters a bed by the hour, and never past the top of
  the culture's band -- a downpour cannot soak it;
* the moment's temperature outside the culture's warmth hurts the bed, and
  colder than the band it sleeps: no growth, no weeds;
* the bed shows the cold and the heat while they last;
* the rain the bed gets is the weather's own at its place, and nothing rains
  off the sphere;
* the sowing gate judges the season's band on the node's own swing;
* nothing is marked, sown or built on ice.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from farm_kit import SPELT, _farmstead, _norms, _weather
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import biome, breed, climate, estate, farm, places, terrain, world
from src.engine.farm import life
from src.engine.farm.settle import _weather as place_weather
from src.models.farm import PlotState
from src.models.world import Node, Planet
from src.units import HARDINESS_SCALE, PERCENT, SCALE_MAX


def _softer(constants: Constants, norm: life.Norms) -> float:
    return 1 - constants[R.FARM_HARDINESS_RELIEF] / PERCENT * norm.hardiness / HARDINESS_SCALE


# --- the rain ----------------------------------------------------------------


def test_the_rain_waters_the_bed_up_to_the_top_of_its_band(
    constants: Constants, catalog: Catalog
) -> None:
    norm = _norms(constants, catalog)
    day = constants[R.TIME_DAY_TERRA]
    reference = constants[R.FARM_DRY_TEMP_REF]
    pour = constants[R.FARM_RAIN_PER_HOUR]
    rate = life.dry_rate(constants, norm, _weather(), reference)
    dry = life.Life(moisture=norm.band_min - 10, health=SCALE_MAX, growth=0.0)

    #: An hour of downpour: the hour's drying, then the rain's points on top.
    wet = life.advance(
        constants,
        norm,
        _weather(temperature=reference, rain=1.0),
        dry,
        hours=1,
        day_hours=day,
        fertility=0,
    )
    left = dry.moisture * math.exp(-rate / day)
    assert wet.moisture == pytest.approx(left + pour, rel=1e-6)
    #: A lighter rain gives its share.
    drizzle = life.advance(
        constants,
        norm,
        _weather(temperature=reference, rain=0.5),
        dry,
        hours=1,
        day_hours=day,
        fertility=0,
    )
    assert drizzle.moisture == pytest.approx(left + pour / 2, rel=1e-6)

    #: A long downpour fills the bed to the top of its band and no further:
    #: the ground sheds the rest, and no fungus comes of the rain.
    days = life.advance(
        constants,
        norm,
        _weather(temperature=reference, rain=1.0),
        dry,
        hours=day * 3,
        day_hours=day,
        fertility=0,
    )
    assert days.moisture == pytest.approx(norm.band_max, abs=pour)
    assert days.moisture <= norm.band_max
    assert days.pest.get(life.FUNGUS, 0.0) == 0

    #: A bed a farmer watered over the top is left to dry as it would have.
    soaked = life.Life(moisture=SCALE_MAX, health=SCALE_MAX, growth=0.0)
    rained = life.advance(
        constants,
        norm,
        _weather(temperature=reference, rain=1.0),
        soaked,
        hours=1,
        day_hours=day,
        fertility=0,
    )
    bare = life.advance(
        constants,
        norm,
        _weather(temperature=reference),
        soaked,
        hours=1,
        day_hours=day,
        fertility=0,
    )
    assert rained.moisture == pytest.approx(bare.moisture)


def test_the_rain_the_bed_gets_is_the_weathers_own_at_its_place(
    constants: Constants,
) -> None:
    """One law (D-335): the bed is watered by the rain the map draws there."""
    epoch = datetime(2026, 1, 1, tzinfo=UTC)
    since = epoch + timedelta(days=3, hours=5)
    lat, lon = 32.66, -105.56
    node = Node(
        key="terra.rain",
        name="rain",
        planet=Planet.TERRA,
        properties={
            "temperature": 18,
            places.PLACE: {places.PLACE_LAT: lat, places.PLACE_LON: lon},
        },
    )
    along = climate.rain_along(constants, node, epoch, since)
    wired = place_weather(constants, node, epoch, since)
    hours = [h / 2 for h in range(0, 24 * 28 * 2, 7)]
    rained = 0
    for hour in hours:
        _, rain = climate.weather_at(
            constants, Planet.TERRA, lat, lon, epoch, since + timedelta(hours=hour)
        )
        assert along(hour) == pytest.approx(rain, abs=1e-12)
        assert wired.rain_at(hour) == pytest.approx(rain, abs=1e-12)
        rained += rain > 0
    assert rained, "a year at the capital without a single rain would test nothing"

    #: Nothing rains off the sphere: a room, a storey, a hull.
    room = Node(key="terra.room", name="room", planet=Planet.TERRA, properties={})
    assert all(climate.rain_along(constants, room, epoch, since)(hour) == 0 for hour in hours)


# --- the warmth --------------------------------------------------------------


def test_the_cold_puts_the_bed_to_sleep_and_the_heat_hurts_it(
    constants: Constants, catalog: Catalog
) -> None:
    norm = _norms(constants, catalog)
    day = constants[R.TIME_DAY_TERRA]
    chill = constants[R.FARM_TEMP_STRESS_PER_DEGREE]
    mid = (norm.band_min + norm.band_max) / 2
    start = life.Life(moisture=mid, health=SCALE_MAX, growth=30.0, weeds=10.0)

    #: Five degrees under the band for an hour: the harm is the degrees', the
    #: growth and the weeds stand still.
    cold = life.advance(
        constants,
        norm,
        _weather(river=True, temperature=norm.temp_min - 5),
        start,
        hours=1,
        day_hours=day,
        fertility=SCALE_MAX,
    )
    assert cold.growth == start.growth
    assert cold.weeds == start.weeds
    assert SCALE_MAX - cold.health == pytest.approx(chill * 5 * _softer(constants, norm) / day)

    #: Three over it: the harm is the degrees', and the bed still grows.
    hot = life.advance(
        constants,
        norm,
        _weather(river=True, temperature=norm.temp_max + 3),
        start,
        hours=1,
        day_hours=day,
        fertility=SCALE_MAX,
    )
    assert hot.growth > start.growth
    assert SCALE_MAX - hot.health == pytest.approx(chill * 3 * _softer(constants, norm) / day)

    #: Inside the warmth and the band a hurt bed mends.
    warm = life.advance(
        constants,
        norm,
        _weather(river=True, temperature=(norm.temp_min + norm.temp_max) / 2),
        replace(start, health=80.0),
        hours=1,
        day_hours=day,
        fertility=SCALE_MAX,
    )
    assert warm.health > 80.0 and warm.growth > start.growth

    #: Hardiness softens the cold as it softens the drought (D-261).
    tough = life.advance(
        constants,
        replace(norm, hardiness=HARDINESS_SCALE),
        _weather(river=True, temperature=norm.temp_min - 5),
        start,
        hours=1,
        day_hours=day,
        fertility=SCALE_MAX,
    )
    assert tough.health > cold.health

    #: A winter left under the band kills.
    winter = life.advance(
        constants,
        norm,
        _weather(river=True, temperature=norm.temp_min - 15),
        start,
        hours=day * 30,
        day_hours=day,
        fertility=0,
    )
    assert winter.dead

    #: A node without a temperature record has no warmth to fall out of.
    unknown = life.advance(
        constants, norm, _weather(river=True), start, hours=1, day_hours=day, fertility=0
    )
    assert unknown.health == SCALE_MAX and unknown.growth > start.growth


def test_the_bed_shows_the_cold_and_the_heat_while_they_last(
    constants: Constants, catalog: Catalog
) -> None:
    norm = _norms(constants, catalog)
    mid = (norm.band_min + norm.band_max) / 2
    bed = life.Life(moisture=mid, health=SCALE_MAX, growth=0.0, thinned=True)

    def seen(temperature: float | None) -> list[str]:
        return life.symptoms(
            constants,
            norm,
            bed,
            fertility=PERCENT,
            fertility_needed=0.0,
            fed=(),
            temperature=temperature,
        )

    assert life.CHILLED in seen(norm.temp_min - 1)
    assert life.WILTED in seen(norm.temp_max + 1)
    assert not {life.CHILLED, life.WILTED} & set(seen((norm.temp_min + norm.temp_max) / 2))
    assert not {life.CHILLED, life.WILTED} & set(seen(None))


def test_the_care_text_says_the_warmth(constants: Constants, catalog: Catalog) -> None:
    plant = catalog.plants.by_id(SPELT)
    text = farm.care_text(constants, plant, breed.traits_of_plant(plant), locale="ru")
    low, high = round(plant.requires.temp["min"]), round(plant.requires.temp["max"])
    assert f"от {low} до {high} °C" in text


# --- the sowing gate ---------------------------------------------------------


async def _plowed(session: AsyncSession, constants: Constants, catalog: Catalog, **properties):
    """A ploughed strip on a node of these properties, and seeds in the hands."""
    node, _, body = await _farmstead(session)
    plot = await farm.mark(session, constants, body, name="strip", area=10)
    plot.state = PlotState.PLOWED
    node.properties = {**node.properties, **properties}
    await session.flush()
    cultivar = await breed.landrace(session, catalog, SPELT)
    pocket = await world.body_container(session, body)
    seeds = await breed.seed_lot(session, catalog, pocket.id, cultivar, 200, PERCENT)
    return node, body, plot, seeds


async def test_the_sowing_gate_judges_the_seasons_band(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The year's band fits and the winter's does not (D-338): refused in
    winter, sown in summer -- the same place, the same crop."""
    plant = catalog.plants.by_id(SPELT)
    #: Dry ground far enough north for a season, and not on the ice (which
    #: refuses the sowing before any gate would).
    lat, lon = next(
        (float(lat), float(lon))
        for lat in range(50, 66, 2)
        for lon in range(-180, 180, 4)
        if biome.classify(constants, Planet.TERRA, lat, lon) not in (None, biome.ICE)
    )
    swing = 2.0
    node, body, plot, seeds = await _plowed(
        session,
        constants,
        catalog,
        temperature=plant.requires.temp["min"] + swing + 1,
        temperature_swing=swing,
        **{places.PLACE: {places.PLACE_LAT: lat, places.PLACE_LON: lon}},
    )
    epoch = await world.epoch(session)
    period = climate.sky.circle_of(constants, Planet.TERRA.value)[1]
    moments = [epoch + timedelta(days=period * step / 40) for step in range(40)]
    by_season = sorted(
        moments, key=lambda moment: climate.season_c(constants, Planet.TERRA, lat, epoch, moment)
    )
    winter, summer = by_season[0], by_season[-1]
    assert climate.season_c(constants, Planet.TERRA, lat, epoch, winter) < -2

    with pytest.raises(farm.WrongClimate) as refused:
        await farm.sow(session, constants, catalog, body, plot, seeds, now=winter)
    assert refused.value.key == "farm-too-cold"
    await farm.sow(session, constants, catalog, body, plot, seeds, now=summer)
    assert plot.state is PlotState.SOWN


async def test_the_sowing_gate_swings_by_the_nodes_own_day(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A place of an even day is sown where the planet's swing would have
    refused it: the gate and the bed live by one band (D-321, D-338)."""
    plant = catalog.plants.by_id(SPELT)
    planet_swing = climate.swing_of(constants, Planet.TERRA)
    own = planet_swing / 2
    #: Off the sphere, so no season moves it: only the swing is under test.
    node, body, plot, seeds = await _plowed(
        session,
        constants,
        catalog,
        temperature=plant.requires.temp["min"] + own + 0.5,
        temperature_swing=own,
    )
    assert plant.requires.temp["min"] + own + 0.5 - planet_swing < plant.requires.temp["min"]
    await farm.sow(session, constants, catalog, body, plot, seeds)
    assert plot.state is PlotState.SOWN


# --- the ice -----------------------------------------------------------------


async def test_nothing_is_marked_or_sown_on_ice(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, _, body = await _farmstead(session)
    node.properties = {**node.properties, biome.BIOME: biome.ICE}
    await session.flush()
    with pytest.raises(farm.FarmError) as refused:
        await farm.mark(session, constants, body, name="strip", area=10)
    assert refused.value.key == "farm-on-ice"

    #: A strip marked before the ice -- the field rebuilt under it -- is not
    #: sown either.
    thawed, body, plot, seeds = await _plowed(
        session, constants, catalog, **{biome.BIOME: biome.ICE}
    )
    with pytest.raises(farm.FarmError) as refused:
        await farm.sow(session, constants, catalog, body, plot, seeds)
    assert refused.value.key == "farm-on-ice"
    assert plot.state is PlotState.PLOWED


async def _yard(session: AsyncSession, **properties):
    """An own civic plot, empty, the owner standing on it (`test_estate_site._plot`)."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session, f"terra.yard.{stamp}", "yard", area_m2=100, properties=properties
    )
    node.owner_city_id = uuid.uuid4()
    await session.flush()
    identity = await world.create_identity(session, f"builder-{stamp}")
    body = await world.print_body(session, identity, node)
    await world.grant_node(session, node, identity)
    return node, body


async def test_nothing_is_built_on_ice(session: AsyncSession, constants: Constants) -> None:
    node, body = await _yard(session, **{biome.BIOME: biome.ICE})
    with pytest.raises(estate.EstateError) as refused:
        await estate.construct(session, constants, body, node, 20)
    assert refused.value.key == "estate-build-on-ice"
    with pytest.raises(estate.EstateError) as refused:
        await estate.lay_site(session, constants, body, node, 20)
    assert refused.value.key == "estate-build-on-ice"

    #: A node of the ordinary ground is laid out as before.
    ground, builder = await _yard(session)
    assert await estate.lay_site(session, constants, builder, ground, 20)


def test_aurora_is_ice_to_its_last_dry_point(constants: Constants) -> None:
    """The planet of one face (D-232): every dry point of Aurora stands on
    ice, its cities' ground included; a room there stands on a floor."""
    field = terrain.field_of(constants, Planet.AURORA)
    dry = next(
        (lat, lon)
        for lat in range(-60, 61, 5)
        for lon in range(-180, 180, 5)
        if not field.is_water(lat, lon)
    )
    ground = Node(
        key="aurora.ground",
        name="ground",
        planet=Planet.AURORA,
        properties={places.PLACE: {places.PLACE_LAT: dry[0], places.PLACE_LON: dry[1]}},
    )
    assert biome.on_ice(constants, ground)
    room = Node(key="aurora.room", name="room", planet=Planet.AURORA, properties={})
    assert not biome.on_ice(constants, room)
