# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The weather's law (D-335, D-336): cloud and rain over a point as a function
of the place and the moment, and of nothing else.

Outside `engine` on purpose, beside `globe` and `field`: the law is arithmetic
with numbers of its own -- the hash's multipliers, the weights of the
octaves, how long a system lives and how large it grows -- and the rule
modules hold no numbers of their own (D-065). Those numbers are the law's
**shape**, not its balance, and they are one on every side: the map draws by
the same function (`weatherGlsl.ts`) and the probe reads it (`weather.ts`),
so a shape changed here alone would rain where the picture shows a clear
sky. What can be balanced is the vault's (`weather.*`, `terrain.wind_belts`)
and is read off the registry into a `WeatherLaw`; the engine's `climate`
binds the law to a planet, the ground under the point and the clock.

The sky is made of **systems** (D-336 item 13): a lattice of cells in
latitude and longitude, a cell the vault's `weather.cell_km` of arc, each
cell bearing one system per life -- born, grown and gone over LIFE_SLICES of
`weather.change_days`, on a phase of its own, a new place, size and texture
each life. A system drifts with the wind of its own row and spins where the
wind shears; the cover of a point is the union of the systems that reach it.
A system's texture is a value noise of two octaves on an **integer hash** --
integer, because float sines differ between a GPU and a CPU and whole numbers
do not. The gain that spreads the cover's heap before the gates is the
vault's (`weather.gain`), because it moves the rain as the gates do.

This copy and the probe's are pinned by the same numbers at the same points
(`tests/test_climate.py` here, `weather.test.ts` on the client); the fragment
is pinned by carrying the probe's very constants (`weatherGlsl.WX_*`), which
`weather.test.ts` finds in its source. The two trees cannot import each
other, and they meet in those numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.constants import Constants
from src.constants import registry as R
from src.units import METRES_PER_KM

#: The law's shape, the client's `weatherGlsl.WX_*`: how many slices a system
#: lives, the smallest and the largest system in cells (at most one and a
#: half, so the three cells about a point are all the systems that reach it),
#: the step the wind's shear is read over, how much an anticyclone clears, how
#: cloudy a system's texture is at its floor, and the weight of the texture's
#: finer octave -- at twice the scale, the coarser taking the rest. The
#: octave's weight is D-335 item 2's; the life and the size are the ones
#: D-336 item 13 describes; the shear's step, the clearing and the floor came
#: with item 13 on both sides at once, and the vault does not name them.
LIFE_SLICES = 2.0
SIZE_MIN = 0.8
SIZE_MAX = 1.5
SHEAR_DEG = 1.0
CLEAR_HIGH = 0.5
TEX_FLOOR = 0.35
OCTAVE_2 = 0.35
#: The hash's multipliers, one per coordinate, and its mixing multiplier: the
#: client's `WX_HASH` and `WX_MIX` (D-335 item 2).
HASH = (1597334677, 3812015801, 2798796415, 3367900313)
MIX = 0x45D9F3B

_MASK = 0xFFFFFFFF


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


def law_of(constants: Constants, radius_m: float) -> WeatherLaw:
    """The law off the book for a planet of this radius, metres: the vault's
    cell on the radius, the rest as written. The client's `weatherLaw` is
    the same arithmetic, but without its fallback law: a book with no cell
    here is a broken book, not a clear sky."""
    cell_m = float(constants[R.WEATHER_CELL_KM]) * METRES_PER_KM
    belts = constants[R.TERRAIN_WIND_BELTS]
    return WeatherLaw(
        cell_deg=360.0 * cell_m / (2.0 * math.pi * radius_m),
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


def _wx_hash(x: int, y: int, z: int, w: int) -> float:
    """The integer hash of a lattice corner and a slice, nought to one in
    steps of a sixteen-millionth: the GPU's own uint arithmetic."""
    n = ((x * HASH[0]) ^ (y * HASH[1]) ^ (z * HASH[2]) ^ (w * HASH[3])) & _MASK
    n = ((n ^ (n >> 16)) * MIX) & _MASK
    n = ((n ^ (n >> 16)) * MIX) & _MASK
    n ^= n >> 16
    return (n & 0xFFFFFF) / 16777216.0


def _smooth(f: float) -> float:
    return f * f * (3.0 - 2.0 * f)


def _belt(x: float) -> float:
    """The rain march's own edge: a smooth step a belt's edge wide, centred."""
    t = min(1.0, max(0.0, x + 0.5))
    return t * t * (3.0 - 2.0 * t)


def _lerp(a: float, b: float, t: float) -> float:
    """The GPU's own mix: exact at both ends."""
    return a * (1.0 - t) + b * t


def _smoothstep(lo: float, hi: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - lo) / max(hi - lo, 1e-9)))
    return t * t * (3.0 - 2.0 * t)


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
            tex = (1.0 - OCTAVE_2) * _wx_noise2(ux, uy, sid, 21) + OCTAVE_2 * _wx_noise2(
                ux * 2.0, uy * 2.0, sid, 22
            )
            tex = TEX_FLOOR + (1.0 - TEX_FLOOR) * tex
            clear = 1.0 - CLEAR_HIGH * max(0.0, -z)
            keep *= 1.0 - tex * env * fall * clear
    return 1.0 - keep


def weather_cover(law: WeatherLaw, lat_deg: float, lon_deg: float, days: float) -> float:
    """The cover spread by the gain about a half: the law's own number."""
    return min(1.0, max(0.0, 0.5 + (weather_sky(law, lat_deg, lon_deg, days) - 0.5) * law.gain))


def snow_cover(
    warmth_c: float,
    rain01: float,
    *,
    line_c: float,
    band_c: float,
    dry_rain: float,
    dry_keep: float,
) -> float:
    """How much of the ground the snow covers, nought to one (D-334, D-338):
    the shader's law (`fragment.ts`) -- white below `line_c` over `band_c`
    degrees, and a dry cold keeping `dry_keep` of it, ramped up to the whole
    by `dry_rain` of the rain share. A band of nought is a hard line."""
    cover = 1.0 - _smoothstep(line_c - band_c, line_c, warmth_c)
    return cover * _lerp(dry_keep, 1.0, _smoothstep(0.0, dry_rain, rain01))


def weather_of(
    law: WeatherLaw, wetness: float, lat_deg: float, lon_deg: float, days: float
) -> tuple[float, float]:
    """How clouded the sky is and how hard it rains, nought to one each, over
    a point (degrees) at so many real days since the epoch, above ground of
    this wetness (the ground's rain share, or the land's mean over the sea): the
    cover stretched by it -- a wet windward slope thickens what the wind
    brings, a dry lee thins it -- and gated by the vault's numbers."""
    cover = min(
        1.0,
        max(
            0.0,
            weather_cover(law, lat_deg, lon_deg, days) * (1.0 + law.bias * (2.0 * wetness - 1.0)),
        ),
    )
    return (
        _smoothstep(law.cloud_from, law.cloud_full, cover),
        _smoothstep(law.rain_from, law.rain_full, cover),
    )
