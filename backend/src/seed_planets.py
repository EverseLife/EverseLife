# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Where the seed pins the few surface nodes it lays itself (D-321).

The surface of a planet is not laid at the world's birth any more: a scout
finds it, cell by cell (`engine/explore`). What the seed still places is the
handful of nodes the layout needs before anybody has walked -- the plateau and
the black fields of Pyroxis (D-233) -- and it places them the way a scout
would: on the lattice, on dry ground, one reach of the biome apart.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src import globe
from src.constants import Constants
from src.constants import registry as R
from src.engine import biome, terrain
from src.engine.explore import cell_of, point_of
from src.models.world import Planet

#: How many points of the sphere are tried for a planet's first dry ground.
TRIES = 144


@dataclass(frozen=True, slots=True)
class Site:
    number: int
    point: globe.Geo


def _spiral(count: int) -> list[globe.Geo]:
    """Points spread evenly over the sphere: the Fibonacci lattice, the same every time."""
    golden = math.pi * (3 - math.sqrt(5))
    points = []
    for i in range(count):
        z = 1 - (2 * i + 1) / count
        lat = math.degrees(math.asin(z))
        lon = math.degrees((i * golden) % math.tau) - 180.0
        points.append((lat, lon))
    return points


def anchor(constants: Constants, planet: Planet) -> globe.Geo | None:
    """The first dry point of a planet nobody seeded a city on."""
    lat_max = float(constants[R.MAP_CITY_LAT_MAX])
    for point in _spiral(TRIES):
        if abs(point[0]) <= lat_max and terrain.is_land(constants, planet, *point):
            return point
    return None


def sites(
    constants: Constants, planet: Planet, count: int, *, taken: list[globe.Geo]
) -> list[Site]:
    """`count` sites round the planet's anchor: the anchor itself, then a fan
    one biome's reach out, each pressed to the lattice a scout would find."""
    if count <= 0:
        return []
    centre = anchor(constants, planet)
    if centre is None:
        return []
    radius = globe.radius_m(constants, planet)
    here = biome.classify(constants, planet, *centre) or biome.OF_PLANET.get(planet, biome.STEPPE)
    #: The biome's band, not the facet's (landscape plan, wave 7): at the
    #: world's birth nothing stands anywhere yet, and this is the spacing of
    #: the first sites, not a reach the scout will be held to.
    near, far = biome.reach_m(constants, here)
    step = globe.midpoint(near, far)
    chosen = [Site(number=1, point=point_of(constants, planet, cell_of(constants, planet, centre)))]
    kept = list(taken) + [chosen[0].point]
    number = 1
    while len(chosen) < count and number <= count * 3:
        angle = math.tau * (number - 1) / max(1, count - 1)
        raw = globe.offset(radius, centre, step * math.cos(angle), step * math.sin(angle))
        point = point_of(constants, planet, cell_of(constants, planet, raw))
        number += 1
        if not terrain.is_land(constants, planet, *point):
            continue
        if any(globe.distance_m(radius, point, other) < near for other in kept):
            continue
        chosen.append(Site(number=len(chosen) + 1, point=point))
        kept.append(point)
    return chosen
