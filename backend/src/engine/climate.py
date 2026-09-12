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
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src import globe, sky
from src.constants import Constants
from src.constants import registry as R
from src.engine import estate, places, terrain, world
from src.models.world import Node, Planet
from src.units import (
    DAY_PHASE_DAWN,
    DAY_PHASE_DUSK,
    FULL_TURN_DEGREES,
    LIGHT_MAX,
    METRES_PER_KM,
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
    days = 0.0 if origin is None else (moment - origin).total_seconds() / (24 * SECONDS_PER_HOUR)
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
    (`temperature_now`), which is what the beds and the window feel; the
    sowing gate and the biome judge by the year's mean still (D-334).
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


#: The weather (D-335): cloud and rain as a field over the sphere that is a
#: function of the place and the moment and of nothing else -- the same
#: function the map draws by (`weatherGlsl.ts`) and the probe reads
#: (`weather.ts`), on the same numbers of the book, so what the picture
#: shows raining is what the engine reads as rain. The lattice is the unit
#: ball scaled to the vault's cell, turned about the pole by the wind's
#: drift as the days go; the field is two slices of a value noise on an
#: **integer hash** blended over `weather.change_days` -- integer, because
#: float sines differ between a GPU and a CPU and whole numbers do not.
#: Two octaves, the finer drifting faster, so the systems turn as they go:
#: the finer at twice the scale and 1.6 of the drift, its slices a thousand
#: apart from the coarser's, weighted 0.65/0.35 -- the law's shape, one on
#: every side (D-335 п. 2); the gain that spreads the noise's heap before
#: the gates is the vault's (`weather.gain`), because it moves the rain as
#: the gates do.
_WX_MASK = 0xFFFFFFFF


def _wx_hash(x: int, y: int, z: int, w: int) -> float:
    n = ((x * 1597334677) ^ (y * 3812015801) ^ (z * 2798796415) ^ (w * 3367900313)) & _WX_MASK
    n = ((n ^ (n >> 16)) * 0x45D9F3B) & _WX_MASK
    n = ((n ^ (n >> 16)) * 0x45D9F3B) & _WX_MASK
    n ^= n >> 16
    return (n & 0xFFFFFF) / 16777216.0


def _smooth(f: float) -> float:
    return f * f * (3.0 - 2.0 * f)


#: What the ground's rain share is taken to be over the sea, where the rain
#: raster holds nought: the neutral half, the fixed point of the wet bias's
#: factor -- the client's `weatherGlsl.WX_SEA_WET`, the law's shape.
SEA_WET = 0.5
#: The law's shape (D-336 item 13), the client's `weatherGlsl.WX_*`: how many
#: slices a system lives, the smallest and the largest system in cells (at
#: most one and a half, so the three cells about a point are all the systems
#: that reach it), the step the wind's shear is read over, how much an
#: anticyclone clears, and how cloudy a system's texture is at its floor.
LIFE_SLICES = 2.0
SIZE_MIN = 0.8
SIZE_MAX = 1.5
SHEAR_DEG = 1.0
CLEAR_HIGH = 0.5
TEX_FLOOR = 0.35


@dataclass(frozen=True)
class WeatherLaw:
    """The weather's numbers for one planet, off the book (D-335, D-336)."""

    #: A cell of the lattice, degrees of arc: `weather.cell_km` on the radius.
    cell_deg: float
    #: The wind's drift, degrees a real day.
    wind_deg: float
    #: One slice, real days; a system lives LIFE_SLICES of them.
    change_days: float
    #: A factor on the cover, not a summand (D-336): the wet windward slope
    #: thickens what the wind brings, the dry lee thins it.
    bias: float
    cloud_from: float
    cloud_full: float
    rain_from: float
    rain_full: float
    gain: float
    #: The belts of the wind, radians of latitude (`terrain.wind_belts`): to
    #: the trades' edge the wind is west, to the westerlies' edge east, past
    #: it west again; the wind turns over `belt_edge` (`weather.belt_edge_deg`),
    #: and that is where it shears and the systems spin.
    trade_lat: float
    westerly_lat: float
    belt_edge: float
    #: How fast a system spins at full shear, radians a real day.
    spin: float


def weather_law(constants: Constants, planet: Planet) -> WeatherLaw:
    """The law for a planet: its own radius under the vault's cell, the rest as written."""
    cell_m = float(constants[R.WEATHER_CELL_KM]) * METRES_PER_KM
    belts = constants[R.TERRAIN_WIND_BELTS]
    return WeatherLaw(
        cell_deg=360.0 * cell_m / (2.0 * math.pi * globe.radius_m(constants, planet)),
        wind_deg=float(constants[R.WEATHER_WIND_DEG_PER_DAY]),
        change_days=max(float(constants[R.WEATHER_CHANGE_DAYS]), 1e-3),
        bias=float(constants[R.WEATHER_WET_BIAS]),
        cloud_from=float(constants[R.WEATHER_CLOUD_FROM]),
        cloud_full=float(constants[R.WEATHER_CLOUD_FULL]),
        rain_from=float(constants[R.WEATHER_RAIN_FROM]),
        rain_full=float(constants[R.WEATHER_RAIN_FULL]),
        gain=float(constants[R.WEATHER_GAIN]),
        trade_lat=math.radians(float(belts["trade_lat"])),
        westerly_lat=math.radians(float(belts["westerly_lat"])),
        belt_edge=math.radians(max(float(constants[R.WEATHER_BELT_EDGE_DEG]), 1e-3)),
        spin=math.radians(float(constants[R.WEATHER_EDDY_TURN_DEG])),
    )


def _belt(x: float) -> float:
    """The rain march's own edge: a smooth step a belt's edge wide, centred."""
    t = min(1.0, max(0.0, x + 0.5))
    return t * t * (3.0 - 2.0 * t)


def _lerp(a: float, b: float, t: float) -> float:
    """The GPU's own mix: exact at both ends."""
    return a * (1.0 - t) + b * t


def _wx_noise2(x: float, y: float, z: int, w: int) -> float:
    """A value noise on the plane, on the system's own corners (z is the system)."""
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = _smooth(x - ix), _smooth(y - iy)
    n0 = _lerp(_wx_hash(ix, iy, z, w), _wx_hash(ix + 1, iy, z, w), fx)
    n1 = _lerp(_wx_hash(ix, iy + 1, z, w), _wx_hash(ix + 1, iy + 1, z, w), fx)
    return _lerp(n0, n1, fy)


def wind_west(law: WeatherLaw, z: float) -> float:
    """How much of the westerlies blow at the latitude given by the ball's
    z: one between the belts' edges, nought in the trades and past the
    westerlies, the edge's own step between (D-336)."""
    a = abs(math.asin(min(1.0, max(-1.0, z))))
    return _belt((a - law.trade_lat) / law.belt_edge) * (
        1.0 - _belt((a - law.westerly_lat) / law.belt_edge)
    )


def wind_east(law: WeatherLaw, lat: float) -> float:
    """The eastward wind at a latitude (radians), minus one to one."""
    return 2.0 * wind_west(law, math.sin(lat)) - 1.0


def wind_shear(law: WeatherLaw, lat: float) -> float:
    """The wind's shear at a latitude, minus one to one: positive where the
    eastward wind falls off poleward (the polar front -- cyclones), negative
    where it rises (the subtropical edge -- anticyclones), nought in the
    middle of a belt."""
    d = math.radians(SHEAR_DEG)
    a = abs(lat)
    s = (wind_east(law, a + d) - wind_east(law, max(0.0, a - d))) / (2.0 * d)
    return min(1.0, max(-1.0, -s * law.belt_edge / 3.0))


def _smoothstep(lo: float, hi: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - lo) / max(hi - lo, 1e-9)))
    return t * t * (3.0 - 2.0 * t)


def weather_sky(law: WeatherLaw, lat_deg: float, lon_deg: float, days: float) -> float:
    """The cover of the sky over a point at so many real days since the
    epoch, nought to one, before the gain and the ground's wetness: the
    union of the systems that reach the point (D-336 item 13). Each cell
    of a lattice in latitude and longitude bears one system per life --
    drifting with the wind of its own row, born and gone on a phase of its
    own, spinning where the wind shears, a new place, size and texture each
    life."""
    cell = law.cell_deg
    n_rows = max(2, round(180.0 / cell))
    dr = 180.0 / n_rows
    r0 = math.floor((lat_deg + 90.0) / dr)
    keep = 1.0
    for r in range(r0 - 1, r0 + 2):
        if r < 0 or r >= n_rows:
            continue
        lat_r = -90.0 + (r + 0.5) * dr
        n_cols = max(4, round(360.0 * math.cos(math.radians(lat_r)) / cell))
        dc = 360.0 / n_cols
        speed = law.wind_deg * wind_east(law, math.radians(lat_r))
        c0 = math.floor((lon_deg - speed * days) / dc)
        for c in range(c0 - 1, c0 + 2):
            cc = c % n_cols
            #: The system's life: which life the cell is on, and how far along.
            phase = _wx_hash(cc, r, 0, 11)
            life = days / (LIFE_SLICES * law.change_days) + phase
            k = math.floor(life)
            age = life - k
            env = math.sin(math.pi * age)
            jx = _wx_hash(cc, r, k, 12) - 0.5
            jy = _wx_hash(cc, r, k, 13) - 0.5
            size = SIZE_MIN + (SIZE_MAX - SIZE_MIN) * _wx_hash(cc, r, k, 14)
            lat_s = lat_r + jy * dr
            lon_s = (c + 0.5 + jx) * dc + speed * days
            dlon = (lon_deg - lon_s + 180.0) % 360.0 - 180.0
            dx = dlon * math.cos(math.radians(lat_s)) / cell
            dy = (lat_deg - lat_s) / cell
            d2 = (dx * dx + dy * dy) / (size * size)
            if d2 >= 1.0:
                continue
            fall = (1.0 - d2) * (1.0 - d2)
            z = wind_shear(law, math.radians(lat_s))
            hemi = 1.0 if lat_s >= 0.0 else -1.0
            #: The spin: faster in the core than at the rim, so the texture
            #: winds into a spiral over the system's life.
            theta = law.spin * z * hemi * age * LIFE_SLICES * law.change_days * (1.0 - d2)
            cs, sn = math.cos(theta), math.sin(theta)
            ux = (dx * cs + dy * sn) / size * 2.0 + 7.0 * jx
            uy = (-dx * sn + dy * cs) / size * 2.0 + 7.0 * jy
            sid = (r * 4096 + cc) * 64 + (k & 63)
            tex = 0.65 * _wx_noise2(ux, uy, sid, 21) + 0.35 * _wx_noise2(
                ux * 2.0, uy * 2.0, sid, 22
            )
            tex = TEX_FLOOR + (1.0 - TEX_FLOOR) * tex
            clear = 1.0 - CLEAR_HIGH * max(0.0, -z)
            keep *= 1.0 - tex * env * fall * clear
    return 1.0 - keep


def weather_cover(law: WeatherLaw, lat_deg: float, lon_deg: float, days: float) -> float:
    """The cover spread by the gain about a half: the law's own number."""
    return min(1.0, max(0.0, 0.5 + (weather_sky(law, lat_deg, lon_deg, days) - 0.5) * law.gain))


def weather_at(
    constants: Constants,
    planet: Planet,
    lat: float,
    lon: float,
    origin: datetime | None,
    moment: datetime,
) -> tuple[float, float]:
    """How clouded the sky is and how hard it rains at a point now, nought to
    one each (D-335): the cover stretched by the field's own rain share -- a
    wet windward slope thickens what the wind brings, a dry lee thins it --
    and gated by the vault's numbers."""
    law = weather_law(constants, planet)
    days = 0.0 if origin is None else (moment - origin).total_seconds() / (24 * SECONDS_PER_HOUR)
    field = terrain.field_of(constants, planet)
    #: Over the sea the rain raster is a hole, not a measure -- the march
    #: records nothing falling onto the sea -- and the sky reads the neutral
    #: half there (the client's `WX_SEA_WET`), or the wet bias thinned every
    #: cloud to the shore and the clouds drew the coasts (owner, 2026-09-13).
    rain01 = SEA_WET if field.is_sea(lat, lon) else float(field.rain_at(lat, lon))
    cover = min(
        1.0, max(0.0, weather_cover(law, lat, lon, days) * (1.0 + law.bias * (2.0 * rain01 - 1.0)))
    )
    return (
        _smoothstep(law.cloud_from, law.cloud_full, cover),
        _smoothstep(law.rain_from, law.rain_full, cover),
    )


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
