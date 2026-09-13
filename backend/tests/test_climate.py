# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The place's climate (D-261).

Checked is what the mechanic is built on:

* the day breathes: midnight is the mean minus the swing, noon plus it,
  and the phase counts from the world's epoch -- the clock the client draws;
* light is the place's sky: the woods take a step, buildings take a step,
  night takes everything;
* a node without a temperature record has no climate and no gate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.engine import climate, world
from src.models.estate import Building
from src.models.world import Planet


async def _place(session: AsyncSession, properties: dict):
    stamp = uuid.uuid4().hex[:8]
    return await world.create_node(
        session, f"terra.sky.{stamp}", "Место", area_m2=100, properties=properties
    )


async def test_the_day_breathes_between_the_swing_edges(
    session: AsyncSession, constants: Constants
) -> None:
    node = await _place(session, {"temperature": 20})
    origin = await world.epoch(session)
    assert origin is not None

    day = climate.day_hours_of(constants, Planet.TERRA)
    swing = climate.swing_of(constants, Planet.TERRA)
    midnight = origin
    noon = origin + timedelta(hours=day / 2)

    assert climate.temperature_now(constants, node, origin, midnight) == pytest.approx(20 - swing)
    assert climate.temperature_now(constants, node, origin, noon) == pytest.approx(20 + swing)
    assert not climate.is_day(constants, Planet.TERRA, origin, midnight)
    assert climate.is_day(constants, Planet.TERRA, origin, noon)


async def test_day_is_when_temperature_runs_above_the_mean(
    session: AsyncSession, constants: Constants
) -> None:
    """The dawn and dusk marks in `units.py` are the diurnal cosine's zero
    crossings: "day" and "the temperature above the node's mean" must stay one
    statement (D-261), or the quarter marks silently detach from the curve
    should its shape ever change."""
    node = await _place(session, {"temperature": 20})
    origin = await world.epoch(session)
    swing = climate.swing_of(constants, Planet.TERRA)
    assert swing > 0, "без размаха у суток нет температурного дня"
    day = climate.day_hours_of(constants, Planet.TERRA)

    #: A prime count of samples never lands on the quarter marks themselves,
    #: where the curve touches the mean and the sign carries no information.
    samples = 97
    for step in range(samples):
        moment = origin + timedelta(hours=day * step / samples)
        above = climate.temperature_now(constants, node, origin, moment) > 20
        assert climate.is_day(constants, Planet.TERRA, origin, moment) == above


async def test_each_planet_counts_its_own_day(constants: Constants) -> None:
    """Terra's 38 hours are nobody else's (OQ-028): the clock and the phase
    must both pick the day by the planet."""
    lengths = {climate.day_hours_of(constants, planet) for planet in Planet}
    assert len(lengths) == len(list(Planet)), "у планет разные сутки"


async def test_light_is_the_places_sky(session: AsyncSession, constants: Constants) -> None:
    """The woods take a step and buildings take a step; night takes everything."""
    open_ground = await _place(session, {"temperature": 20})
    assert await climate.daylight(session, constants, open_ground) == climate.FULL_LIGHT

    grove = await _place(session, {"temperature": 20, "woods": True})
    assert await climate.daylight(session, constants, grove) == climate.FULL_LIGHT - 1

    #: Built over past the share: the yard loses another step of sky.
    session.add(Building(node_id=grove.id, area_m2=30, footprint_m2=30, floors=1))
    await session.flush()
    assert await climate.daylight(session, constants, grove) == climate.FULL_LIGHT - 2

    origin = await world.epoch(session)
    midnight = origin
    assert await climate.light_now(session, constants, grove, origin, midnight) == 0


async def test_a_node_without_a_record_has_no_climate(
    session: AsyncSession, constants: Constants
) -> None:
    bare = await _place(session, {"fertility": 40})
    assert climate.mean_temperature(bare) is None
    assert climate.temperature_now(constants, bare, datetime.now(UTC), datetime.now(UTC)) is None
    assert climate.precipitation(bare) == 0.0


async def test_the_season_swings_with_the_orbit_and_the_latitude(constants: Constants) -> None:
    """The season (D-334): the pole swings the whole way, the equator not at
    all, the hemispheres opposite, and the sun stands over the tilt at
    midsummer -- all by the sky's own angle of the orbit."""
    import math

    from src.constants import registry as R

    origin = datetime(2026, 1, 1, tzinfo=UTC)
    period = float(constants[R.ORBIT_PERIOD_DAYS]["terra"])
    birth = float(constants[R.ORBIT_PHASE]["terra"]) / math.tau
    swing = float(constants[R.SEASON_SWING_C]["terra"])
    tilt = float(constants[R.SEASON_TILT_DEG]["terra"])
    #: A quarter of a turn past the equinox: the north's midsummer.
    midsummer = origin + timedelta(days=((0.25 - birth) % 1.0) * period)
    assert climate.orbit_turns(constants, Planet.TERRA, origin, midsummer) == pytest.approx(0.25)
    assert climate.season_c(constants, Planet.TERRA, 90, origin, midsummer) == pytest.approx(swing)
    south = climate.season_c(constants, Planet.TERRA, -90, origin, midsummer)
    assert south == pytest.approx(-swing)
    assert climate.season_c(constants, Planet.TERRA, 0, origin, midsummer) == pytest.approx(0)
    assert climate.sun_latitude(constants, Planet.TERRA, origin, midsummer) == pytest.approx(tilt)
    #: Half a year on, the south's: everything turned about.
    midwinter = midsummer + timedelta(days=period / 2)
    assert climate.season_c(constants, Planet.TERRA, 90, origin, midwinter) == pytest.approx(-swing)
    assert climate.sun_latitude(constants, Planet.TERRA, origin, midwinter) == pytest.approx(-tilt)
    #: No epoch: the world stands at its birth, which is the phase the sky
    #: was born at (`useSky` puts the planet there too).
    assert climate.season_c(constants, Planet.TERRA, 90, None, midsummer) == pytest.approx(
        swing * math.sin(float(constants[R.ORBIT_PHASE]["terra"]))
    )


async def test_the_place_feels_its_season(session: AsyncSession, constants: Constants) -> None:
    """The temperature of the moment is the season's mean of the latitude
    with the day breathing about it (D-334): a place at sixty degrees north
    is warmer at midsummer's midnight than its year's mean less the swing."""
    import math

    from src.constants import registry as R
    from src.engine import places

    north = await _place(
        session,
        {"temperature": 20, places.PLACE: {places.PLACE_LAT: 60, places.PLACE_LON: 0}},
    )
    origin = await world.epoch(session)
    assert origin is not None
    period = float(constants[R.ORBIT_PERIOD_DAYS]["terra"])
    birth = float(constants[R.ORBIT_PHASE]["terra"]) / math.tau
    midsummer = origin + timedelta(days=((0.25 - birth) % 1.0) * period)
    swing = climate.swing_of(constants, Planet.TERRA)
    season = climate.season_c(constants, Planet.TERRA, 60, origin, midsummer)
    assert season == pytest.approx(
        float(constants[R.SEASON_SWING_C]["terra"]) * math.sin(math.radians(60))
    )
    assert season > 0
    #: Midsummer's own midnight: the day's phase is by the longitude, nought here.
    day = climate.day_hours_of(constants, Planet.TERRA)
    midnight = midsummer + timedelta(
        hours=day * (1 - climate.day_phase(constants, Planet.TERRA, origin, midsummer))
    )
    assert climate.temperature_now(constants, north, origin, midnight) == pytest.approx(
        20 + climate.season_c(constants, Planet.TERRA, 60, origin, midnight) - swing
    )
    #: Off the sphere -- a room -- there is no latitude and no season.
    room = await _place(session, {"temperature": 20})
    assert climate.latitude_of(room) == 0
    assert climate.season_c(constants, Planet.TERRA, 0, origin, midnight) == 0


async def test_the_weather_is_one_law_everywhere(
    constants: Constants, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The weather (D-335, D-336) is a function of the place and the moment
    and of nothing else, on an integer hash: what the map draws the engine
    reads. The numbers here are the ones the client's `weather.test.ts`
    pins too."""
    import math

    from src.weather import WeatherLaw, _wx_hash, weather_cover, weather_sky, wind_shear

    assert _wx_hash(3, -7, 12, 5) == pytest.approx(0.4038313031196594, abs=1e-12)
    assert _wx_hash(0, 0, 0, 0) == 0
    law = WeatherLaw(
        cell_deg=360.0 * 3000.0 / (2.0 * math.pi * 12_000.0),
        wind_deg=90.0,
        change_days=1.5,
        bias=0.4,
        cloud_from=0.45,
        cloud_full=0.65,
        rain_from=0.6,
        rain_full=0.85,
        gain=2.0,
        trade_lat=math.radians(30),
        westerly_lat=math.radians(60),
        belt_edge=math.radians(25),
        spin=math.radians(90),
    )
    assert law.cell_deg == pytest.approx(14.32394487827058)
    assert weather_cover(law, 32.66, -105.56, 0.0) == pytest.approx(GOLD[0])
    assert weather_cover(law, 32.66, -105.56, 0.74) == pytest.approx(GOLD[1])
    assert weather_cover(law, -60.0, 20.0, 3.3) == pytest.approx(GOLD[2])
    assert weather_cover(law, 0.0, 0.0, 12.25) == pytest.approx(GOLD[3])
    assert weather_cover(law, 45.0, 10.0, 2.2) == pytest.approx(GOLD[4])
    #: The shear: cyclonic on the polar front, anticyclonic on the
    #: subtropical edge, nought in the middle of a belt.
    assert wind_shear(law, math.radians(60)) > 0.5
    assert wind_shear(law, math.radians(30)) < -0.5
    assert wind_shear(law, math.radians(45)) == 0.0
    #: Continuous over place and time, bounded, and moving: the same place
    #: another day is another sky.
    here = weather_sky(law, 20.0, 40.0, 1.5)
    assert abs(weather_sky(law, 20.001, 40.0, 1.5) - here) < 2e-3
    assert abs(weather_sky(law, 20.0, 40.001, 1.5) - here) < 2e-3
    assert abs(weather_sky(law, 20.0, 40.0, 1.5001) - here) < 2e-3
    assert 0.0 <= here <= 1.0
    assert weather_sky(law, 10.0, 10.0, 2.0) != weather_sky(law, 10.0, 10.0, 5.0)
    #: On the planet itself, off the book: the capital's sky now.
    origin = datetime(2026, 1, 1, tzinfo=UTC)
    cloud, rain = climate.weather_at(constants, Planet.TERRA, 32.66, -105.56, origin, origin)
    assert 0.0 <= cloud <= 1.0 and 0.0 <= rain <= 1.0
    #: Rain wants cloud: nothing rains out of a clear sky.
    assert rain <= cloud or rain == 0.0
    #: Over the sea the raster is a hole, not a measure (D-336 item 10): the
    #: sky there reads the neutral half whatever the raster says.
    from src.engine import terrain

    field = terrain.field_of(constants, Planet.TERRA)
    at_sea = next((0.0, float(lon)) for lon in range(-180, 180, 5) if field.is_sea(0.0, lon))
    answers = []
    for share in (0.0, 1.0):
        monkeypatch.setattr(type(field), "rain_at", lambda self, lat, lon, share=share: share)
        answers.append(climate.weather_at(constants, Planet.TERRA, *at_sea, origin, origin))
    assert answers[0] == answers[1]


#: The law's numbers at five points, the ones the client pins (`weather.test.ts`).
GOLD = [0.2320697855030931, 0.0, 0.09181303824912512, 0.7960877399788777, 0.0]
