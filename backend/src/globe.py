# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The sphere: where a point on a planet is, and how far two of them lie apart (D-319).

Outside `engine` on purpose, beside `astro` and `units`: this is arithmetic
with its own numbers -- the last latitude, the wrap of a longitude -- and the
rule modules hold no numbers of their own (D-065). The radius alone is the
vault's, and it is read here from the registry.

A node of a planet's surface stands at a latitude and a longitude, in degrees,
on a sphere whose radius comes from how much **land** the vault gives the world
(`planet.land_area_share` on `planet.earth_radius_km`, D-324) -- not from the
body it is in the sky, which is a different size on purpose. Everything
here is arithmetic over those two numbers: no session, no clock, no rounding
into map units -- the client fits degrees into whatever frame it draws.

Great-circle distances by the haversine formula; offsets on the tangent plane
are turned into degrees by the same radius. Good to a metre over the few
kilometres a city and its surroundings span, which is all the surface needs
until wave 5 of the plan reads the sky in the same kilometres.
"""

from __future__ import annotations

import math

import numpy as np

from src.constants import Constants
from src.constants import registry as R
from src.models.world import Planet
from src.units import METRES_PER_KM

#: A point on the sphere: latitude, longitude, degrees.
Geo = tuple[float, float]

#: The last usable latitude: "north up" is not defined on the pole itself, and
#: a seat pushed past it would come back round the other side.
LAST_LAT = 89.0
#: A full turn and a half turn of longitude, for wrapping.
HALF_TURN = 180.0
FULL_TURN = 360.0
QUARTER_TURN = 90.0
#: How many points along a straight way are read for what it crosses.
WAY_SAMPLES = 8
#: How many directions the compass is read in when looking round a point:
#: the ring a patch of ground is ranked against (landscape plan wave 7).
COMPASS_POINTS = 8
#: The golden angle: points fanned round a centre without a pattern.
GOLDEN_ANGLE = math.pi * (3 - math.sqrt(5))
#: Below this the cosine of the latitude is treated as at the pole.
COS_FLOOR = 1e-9


def radius_m(constants: Constants, planet: Planet) -> float:
    """The radius of the land one walks, in metres (D-324).

    Not the body in the sky: a world has two sizes now, and this is the one
    the map, the walk and the scout mean. It comes from how much **land** the
    vault gives the world -- a share of the Earth's surface, chosen for
    crowding and not for physics -- and a sphere of that area has this radius.
    A world the vault forgot gets the Earth's own land: absurdly large, and
    therefore noticed rather than quietly wrong.
    """
    share = float(constants[R.PLANET_LAND_AREA_SHARE].get(planet.value, 1.0))
    return math.sqrt(share) * float(constants[R.PLANET_EARTH_RADIUS_KM]) * METRES_PER_KM


def distance_m(radius: float, one: Geo, other: Geo) -> float:
    """The great-circle distance between two points of a sphere of that radius."""
    lat1, lon1 = math.radians(one[0]), math.radians(one[1])
    lat2, lon2 = math.radians(other[0]), math.radians(other[1])
    sin_lat = math.sin((lat2 - lat1) / 2)
    sin_lon = math.sin((lon2 - lon1) / 2)
    chord = sin_lat * sin_lat + math.cos(lat1) * math.cos(lat2) * sin_lon * sin_lon
    return 2 * radius * math.asin(min(1.0, math.sqrt(chord)))


def offset(radius: float, at: Geo, east_m: float, north_m: float) -> Geo:
    """The point `east_m` east and `north_m` north of `at`, on the tangent plane.

    The longitude step shrinks with the cosine of the latitude, so a step east
    is the same metres at the equator and near the pole; the latitude is held
    short of the pole itself.
    """
    lat = math.radians(at[0])
    d_lat = math.degrees(north_m / radius)
    d_lon = math.degrees(east_m / (radius * max(math.cos(lat), 1e-9)))
    new_lat = max(-LAST_LAT, min(LAST_LAT, at[0] + d_lat))
    new_lon = ((at[1] + d_lon + 180.0) % 360.0) - 180.0
    return (new_lat, new_lon)


def bearing(one: Geo, other: Geo) -> float:
    """The way from `one` to `other` as one sets out: degrees clockwise from north."""
    lat1, lat2 = math.radians(one[0]), math.radians(other[0])
    dlon = math.radians(wrap_lon(other[1] - one[1]))
    east = math.sin(dlon) * math.cos(lat2)
    north = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(east, north)) % FULL_TURN


def wrap_lon(lon: float) -> float:
    """A longitude brought into the half-open turn round the zero meridian."""
    return ((lon + HALF_TURN) % FULL_TURN) - HALF_TURN


def walk_between(a: Geo, b: Geo, share: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """`between` for a whole walk at once: the points a share of the way from
    one to the other, the short way round."""
    turn = wrap_lon(b[1] - a[1])
    return a[0] + (b[0] - a[0]) * share, (a[1] + turn * share + HALF_TURN) % FULL_TURN - HALF_TURN


def between(a: Geo, b: Geo, share: float) -> Geo:
    """The point `share` of the way from `a` to `b`, the short way round."""
    dlon = wrap_lon(b[1] - a[1])
    return (a[0] + (b[0] - a[0]) * share, wrap_lon(a[1] + dlon * share))


def lon_stretch(lat: float) -> float:
    """How many degrees of longitude one degree of latitude's metres make here."""
    return 1 / max(math.cos(math.radians(lat)), COS_FLOOR)


def midpoint(a: float, b: float) -> float:
    """Halfway between two lengths."""
    return (a + b) / 2


def radius_of_area(area_m2: float) -> float:
    """The radius of the circle an area makes: a node's footprint on the map."""
    return math.sqrt(float(area_m2) / math.pi)
