# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The season's snow on a way (D-338).

Kept apart from `_base` on purpose: the floor of the travel package reads no
climate, and the climate reads the estate, which walks back into travel. The
walk, the route and the exits ask here; the multiplier the snow is paid by
is the floor's (`_base.snow_multiplier`).
"""

from __future__ import annotations

from datetime import datetime

from src import globe
from src.constants import Constants
from src.engine import climate, places
from src.engine.transport import OFF_ROAD
from src.models.world import Edge, Node, Planet

#: Where an end of a way lies: its planet and its point, or no point off the sphere.
Place = tuple[Planet, globe.Geo | None]


def place_of(node: Node | None) -> Place | None:
    return None if node is None else (node.planet, places.geo_of(node))


def edge_snow(
    constants: Constants,
    edge: Edge,
    ends: tuple[Place | None, Place | None],
    origin: datetime | None,
    moment: datetime,
) -> float:
    """The snow an edge is walked through, nought to one: the mean of the
    season's snow on its two ends (`climate.snow_now`), the way running from
    the one to the other. A road is not asked at all -- the snow does not
    lengthen it -- and an end off the sphere has no ground to lie on."""
    if edge.surface not in OFF_ROAD:
        return 0.0
    lying = [
        0.0 if point is None else climate.snow_now(constants, planet, *point, origin, moment)
        for planet, point in (end for end in ends if end is not None)
    ]
    return sum(lying) / len(lying) if lying else 0.0
