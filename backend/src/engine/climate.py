# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The place's climate as farming reads it (D-261).

Exploration has written every found site a mean `temperature` and
`precipitation` since D-126; the planet adds the diurnal swing
(`planet.temp_swing`), and the planetary day (`time.day_*`) sets the phase:
noon is the peak, midnight the floor. Light is a 0-3 scale that breathes the
same day: 3 in the open, a step less under the woods, a step less again where
the ground is built over (`farm.shade_built_share`), nought at night.

The phase counts from the world's epoch (`world.epoch`, D-029) -- the same
origin the client's planetary clock ticks from, so the server's "now" and the
drawn hand never disagree.

The sowing gate compares the culture's requirements against the node's
**daily band**, not the moment of sowing: a crop lives through every hour of
its cycle, so what must fit is the whole swing. A node without a temperature
-- old ones, homes, a ship's hydroponics bay -- carries no gate: absence of a
record is not a climate.

Nothing here writes: the current temperature is a pure function of the clock,
so a read stays a read (the quality bar's "look does not write").
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src import globe, sky, weather
from src.constants import Constants
from src.constants import registry as R
from src.engine import estate, places, terrain, world
from src.models.world import Node, Planet
from src.runtime import HOURS_KEPT
from src.units import (
    DAY_PHASE_DAWN,
    DAY_PHASE_DUSK,
    FULL_TURN_DEGREES,
    HOURS_PER_DAY,
    LIGHT_MAX,
    PERCENT,
    SECONDS_PER_HOUR,
)

#: The light scale's ceiling: an open clearing at noon. Matches the catalog's
#: `requires.light` 1-3, with nought left for the night; the scale itself is
#: representation and lives in `units.LIGHT_MAX`.
FULL_LIGHT = LIGHT_MAX

#: The place mark the world's generation writes for a forest (`ground.WOODS`,
#: D-191). Named here like farm's WATER: importing ground for one word would
#: put the whole roll on this module's import path.
WOODS = "woods"

#: The vault's day length per planet (OQ-028). One map here rather than four
#: lookups in callers: the registry key is picked by the node's planet.
_DAY_OF = {
    Planet.TERRA: R.TIME_DAY_TERRA,
    Planet.AQUATICA: R.TIME_DAY_AQUATICA,
    Planet.PYROXIS: R.TIME_DAY_PYROXIS,
    Planet.AURORA: R.TIME_DAY_AURORA,
}


def day_hours_of(constants: Constants, planet: Planet) -> float:
    return constants[_DAY_OF[planet]]


def swing_of(constants: Constants, planet: Planet, node: Node | None = None) -> float:
    """The diurnal temperature swing around the node's mean (D-261).

    A found node carries its biome's own swing (D-321, `temperature_swing`):
    the desert is hot by day and cold by night where the forest is even. A
    node without one -- seeded, or a room -- swings as its planet does.
    """
    own = (node.properties or {}).get("temperature_swing") if node is not None else None
    if own is not None:
        try:
            return float(own)
        except (TypeError, ValueError):  # pragma: no cover -- properties are engine-written
            pass
    return float(constants[R.PLANET_TEMP_SWING].get(planet.value, 0.0))


def mean_temperature(node: Node) -> float | None:
    """The node's mean temperature, if exploration ever wrote one."""
    raw = (node.properties or {}).get("temperature")
    try:
        return None if raw is None else float(raw)
    except (TypeError, ValueError):  # pragma: no cover -- properties are engine-written
        return None


def precipitation(node: Node) -> float:
    """The node's rainfall on the 0-100 scale. Absent reads as dry."""
    raw = (node.properties or {}).get("precipitation")
    try:
        return max(0.0, float(raw or 0))
    except (TypeError, ValueError):  # pragma: no cover
        return 0.0


def day_phase(
    constants: Constants,
    planet: Planet,
    origin: datetime | None,
    moment: datetime,
    *,
    longitude: float = 0.0,
) -> float:
    """Where in the planetary day the moment falls: 0 is midnight, 0.5 noon.

    Counted from the world's epoch so the server and the client's clock agree
    on the hour; a world with no epoch yet has no first node and nothing to
    farm, and reads as its own midnight. The planet turns (D-319): noon comes
    to a place east of the meridian first, by its longitude's share of the
    turn, so two cities on one planet do not share a dawn.
    """
    if origin is None:
        return 0.0
    day_seconds = day_hours_of(constants, planet) * SECONDS_PER_HOUR
    turned = ((moment - origin).total_seconds() % day_seconds) / day_seconds
    return (turned + longitude / FULL_TURN_DEGREES) % 1


def is_day(
    constants: Constants,
    planet: Planet,
    origin: datetime | None,
    moment: datetime,
    *,
    longitude: float = 0.0,
) -> bool:
    """The lit half of the planetary day: the middle two quarters."""
    phase = day_phase(constants, planet, origin, moment, longitude=longitude)
    return DAY_PHASE_DAWN <= phase < DAY_PHASE_DUSK


def longitude_of(node: Node) -> float:
    """Where the node's noon is measured from: its longitude, or the meridian off the sphere."""
    point = places.geo_of(node)
    return 0.0 if point is None else point[1]


def latitude_of(node: Node) -> float:
    """Where the node's season is measured from: its latitude, or the equator off the sphere."""
    point = places.geo_of(node)
    return 0.0 if point is None else point[0]


def day_index(
    constants: Constants, planet: Planet, origin: datetime | None, moment: datetime
) -> int:
    """Which calendar day of the planet the moment falls in, counted from the
    world's epoch (D-263).

    The farm round goes by this number, not by an interval: one day -- one
    round, at any hour of it, so the care window never drifts away from a
    player's own rhythm. A world with no epoch has nothing to farm and lives
    in its day nought.
    """
    if origin is None:
        return 0
    day_seconds = day_hours_of(constants, planet) * SECONDS_PER_HOUR
    return int((moment - origin).total_seconds() // day_seconds)


def temperature_now(
    constants: Constants, node: Node, origin: datetime | None, moment: datetime
) -> float | None:
    """The node's temperature at the moment: the season's mean of its latitude,
    minus the swing at midnight, plus at noon (D-261, D-334).

    The mean the node carries is the year's; the season moves it by the
    latitude and the orbit's angle (`season_c`), and the day breathes about
    **that** -- so a bed at sixty degrees dries at a winter's pace in winter
    and the window's "now" agrees with the snow the map draws there.
    """
    mean = mean_temperature(node)
    if mean is None:
        return None
    swing = swing_of(constants, node.planet, node)
    phase = day_phase(constants, node.planet, origin, moment, longitude=longitude_of(node))
    season = season_c(constants, node.planet, latitude_of(node), origin, moment)
    return mean + season - swing * math.cos(math.tau * phase)


def _days_since(origin: datetime | None, moment: datetime) -> float:
    """Real days from the epoch: what the year and the weather are counted in."""
    if origin is None:
        return 0.0
    return (moment - origin).total_seconds() / (HOURS_PER_DAY * SECONDS_PER_HOUR)


def day_band(
    constants: Constants, node: Node, origin: datetime | None, moment: datetime
) -> tuple[float, float] | None:
    """The node's night and noon on the planetary day of the moment, degrees
    (D-261, D-338): the year's mean moved by the latitude's season, swinging by
    the node's own day (D-321). What the sowing gate fits a culture's warmth
    into and what a bed's cold and heat signs are read by. The season is taken
    as it stood when the day began (the calendar day of D-263), so the band is
    one for the whole day and moves once a day. None where no temperature was
    ever written: absence of a record is not a climate.
    """
    mean = mean_temperature(node)
    if mean is None:
        return None
    day = moment
    if origin is not None:
        length = timedelta(hours=day_hours_of(constants, node.planet))
        day = origin + length * day_index(constants, node.planet, origin, moment)
    mean += season_c(constants, node.planet, latitude_of(node), origin, day)
    swing = swing_of(constants, node.planet, node)
    return mean - swing, mean + swing


def orbit_turns(
    constants: Constants, planet: Planet, origin: datetime | None, moment: datetime
) -> float:
    """Where the planet stands on its orbit, in turns of the circle from the equinox.

    The sky's own count (D-271, `sky.circle_of` -- the year and the phase are
    read there and nowhere else): the phase the world was born at plus the
    **real** days gone since, over the planet's year -- the same figures the
    client turns the planets by (`useSky`), so the season the map draws and
    the season the engine reads are one. A world with no epoch stands at its
    birth, which is the phase.
    """
    _, period, phase = sky.circle_of(constants, planet.value)
    days = _days_since(origin, moment)
    return (phase / math.tau + days / period) % 1.0


def season_c(
    constants: Constants,
    planet: Planet,
    latitude: float,
    origin: datetime | None,
    moment: datetime,
) -> float:
    """The season's offset of the mean temperature at a latitude, degrees (D-334).

    `season.swing_c` at the pole, the sine of the latitude of it elsewhere,
    the sine of the orbit's angle in time: the equator knows no season, the
    two hemispheres run opposite, and the north's summer is where the sine
    of the angle is positive. Read into the temperature of the moment
    (`temperature_now`), which is what the beds and the window feel, and
    into the day's band the sowing gate and a bed's signs judge (`day_band`,
    D-338); the biome judges by the year's mean still (D-334).
    """
    swing = float(constants[R.SEASON_SWING_C].get(planet.value, 0.0))
    turns = orbit_turns(constants, planet, origin, moment)
    return swing * math.sin(math.radians(latitude)) * math.sin(math.tau * turns)


def sun_latitude(
    constants: Constants, planet: Planet, origin: datetime | None, moment: datetime
) -> float:
    """The latitude the sun stands over at the moment, degrees (D-334): the
    tilt's sine by the season's, as far north as the tilt at midsummer."""
    tilt = math.radians(float(constants[R.SEASON_TILT_DEG].get(planet.value, 0.0)))
    turns = orbit_turns(constants, planet, origin, moment)
    return math.degrees(math.asin(math.sin(tilt) * math.sin(math.tau * turns)))


def weather_law(constants: Constants, planet: Planet) -> weather.WeatherLaw:
    """The weather's law for a planet (D-335, D-336): its own radius under the
    vault's cell, the rest as written. The law's shape is `src/weather.py`'s,
    outside the rules (D-065); its balance is the vault's."""
    return weather.law_of(constants, globe.radius_m(constants, planet))


def sky_wetness(constants: Constants, planet: Planet, lat: float, lon: float) -> float:
    """The ground's rain share the sky over a point is stretched by (D-336).

    Over the sea the rain raster is a hole, not a measure -- the march
    records nothing falling onto the sea -- and the sky reads the neutral
    half there (`weather.SEA_WET`, the client's `WX_SEA_WET`), or the wet
    bias thinned every cloud to the shore and the clouds drew the coasts
    (owner, 2026-09-13). A property of the place, not of the moment.
    """
    field = terrain.field_of(constants, planet)
    return weather.SEA_WET if field.is_sea(lat, lon) else float(field.rain_at(lat, lon))


def weather_at(
    constants: Constants,
    planet: Planet,
    lat: float,
    lon: float,
    origin: datetime | None,
    moment: datetime,
) -> tuple[float, float]:
    """How clouded the sky is and how hard it rains at a point now, nought to
    one each (D-335): `weather.weather_of` on the planet's law and the
    place's wetness."""
    return weather.weather_of(
        weather_law(constants, planet),
        sky_wetness(constants, planet, lat, lon),
        lat,
        lon,
        _days_since(origin, moment),
    )


#: What the engine reads of a place over and over, by the world hour (D-338):
#: the tick walks every growing bed each minute from a stamp up to a Terran
#: day old, and a route weighs every off-road edge by its snow. The hours
#: behind are the same for every walk, every bed of the place and every edge
#: of it, and only the newest is new. Two stores and not one: a route over a
#: large world would otherwise push the tick's rain -- the dear reading -- out
#: of a shared one. Keyed by the book's digest and the epoch, so a reloaded
#: book or another world never reads a stale hour.
_Hour = tuple[str, str, float, float, datetime | None, int]
_RAIN_HOURS: OrderedDict[_Hour, float] = OrderedDict()
_SNOW_HOURS: OrderedDict[_Hour, float] = OrderedDict()


def _hourly(
    store: OrderedDict[_Hour, float],
    constants: Constants,
    planet: Planet,
    point: tuple[float, float],
    origin: datetime | None,
    hour: int,
    work: Callable[[], float],
) -> float:
    key = (constants.digest, planet.value, point[0], point[1], origin, hour)
    value = store.get(key)
    if value is None:
        value = work()
        store[key] = value
        if len(store) > HOURS_KEPT:
            store.popitem(last=False)
    else:
        store.move_to_end(key)
    return value


def rain_along(
    constants: Constants, node: Node, origin: datetime | None, since: datetime
) -> Callable[[float], float]:
    """The rain on the node as a function of the hours after `since`, nought
    to one (D-338): the weather's own law at the node's place, the rain the
    map draws there, taken at the start of each world hour -- the hour is
    the bed's step (`FARM_STEP_HOURS`), and so the walk of every bed of the
    node asks the same hours and the law is worked out once for each.
    Nothing rains off the sphere: in a room, on a storey, aboard a hull.
    """
    point = places.geo_of(node)
    if point is None:
        return lambda _hours: 0.0
    planet = node.planet
    start = _days_since(origin, since) * HOURS_PER_DAY

    def at(hours: float) -> float:
        hour = math.floor(start + hours)

        def work() -> float:
            law = weather_law(constants, planet)
            wetness = sky_wetness(constants, planet, *point)
            return weather.weather_of(law, wetness, *point, hour / HOURS_PER_DAY)[1]

        return _hourly(_RAIN_HOURS, constants, planet, point, origin, hour, work)

    return at


def snow_now(
    constants: Constants,
    planet: Planet,
    lat: float,
    lon: float,
    origin: datetime | None,
    moment: datetime,
) -> float:
    """How much of the ground the season's snow covers at a point, nought to
    one (D-334, D-338): the map's own law of snow (`weather.snow_cover`, the
    shader's `fragment.ts`) on the field's readings -- the year's mean of the
    place moved by the latitude's season. The day does not melt it: the map
    draws no swing either. Water is not snowed over -- ice lies there, and
    nobody walks it. Taken at the start of the world hour, as the rain.
    """
    field = terrain.field_of(constants, planet)
    if field.is_water(lat, lon):
        return 0.0
    hour = math.floor(_days_since(origin, moment) * HOURS_PER_DAY)

    def work() -> float:
        at = moment if origin is None else origin + timedelta(hours=hour)
        return weather.snow_cover(
            float(field.temperature_at(lat, lon)) + season_c(constants, planet, lat, origin, at),
            float(field.rain_at(lat, lon)),
            line_c=float(constants[R.SEASON_SNOW_C]),
            band_c=float(constants[R.SEASON_SNOW_BAND_C]),
            dry_rain=float(constants[R.SEASON_SNOW_DRY_RAIN]) / PERCENT,
            dry_keep=float(constants[R.SEASON_SNOW_DRY_SHARE]) / PERCENT,
        )

    return _hourly(_SNOW_HOURS, constants, planet, (lat, lon), origin, hour, work)


def snow_on(constants: Constants, node: Node, origin: datetime | None, moment: datetime) -> float:
    """The season's snow on the node's ground, nought to one (D-338): none off
    the sphere -- a room, a storey, a hull has a floor, not a ground."""
    point = places.geo_of(node)
    if point is None:
        return 0.0
    return snow_now(constants, node.planet, point[0], point[1], origin, moment)


async def daylight(session: AsyncSession, constants: Constants, node: Node) -> int:
    """The node's light at the height of day, 0-3 (D-261).

    The woods take a step, and so does ground built over past
    `farm.shade_built_share`: clearings and meadows keep the whole sky.
    """
    light = FULL_LIGHT
    if world.has_place(node, WOODS):
        light -= 1
    area = float(node.area_m2 or 0)
    if area > 0:
        built = await estate.built_area(session, node, ground=True)
        if built / area * PERCENT >= constants[R.FARM_SHADE_BUILT_SHARE]:
            light -= 1
    return max(0, light)


async def light_now(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    origin: datetime | None,
    moment: datetime,
) -> int:
    """The light this very moment: the day's level, or nought at night."""
    if not is_day(constants, node.planet, origin, moment, longitude=longitude_of(node)):
        return 0
    return await daylight(session, constants, node)
