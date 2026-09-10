# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Which face of its biome a point wears (landscape plan wave 7, §6).

Standing in a forest one is always in a forest -- but in the thicket, on the
edge, on a burn or in a windfall. That grain is the **facet**: a couple of
hundred metres across, a name of its own, and its own numbers. It never
changes the biome; it chooses the biome's face and bends the biome's figures:
what grows and lies about (`marks`), how often a vein turns up (`vein_k`), how
far one may scout from here (`reach_k`) and how wide the day's swing is
(`swing_k`).

The choice is the vault's, not this module's: the rows are `data/facets.yaml`
(`catalog.facets`), and each says where it sits in three axes and what share of
its biome it takes. Four readings of the point decide, and there is nothing
else in it -- no history, no season, no player:

* `noise` -- fine noise whose first octave is `wave_m` across, the mosaic;
* `slope` -- how steep it is here, 0 flat, 1 a wall (`slope_full` is the one);
* `wet` -- how near the water is, 1 at its edge, 0 past `wet_km`;
* `high` -- where the point stands inside its own patch (`patch_km`), 0 the
  bottom of a hollow, 1 the crown of a rise.

So the same point is the same facet on every server and for ever, as the
biome is (D-237): the field is a file and the noise is a function of the seed.
Honest about the grain: two finds three hundred metres apart read differently,
two twenty metres apart usually do not (the owner, 2026-09-09).
"""

from __future__ import annotations

import math

import numpy as np

from src import globe, relief
from src.constants import Constants
from src.constants import registry as R
from src.constants.catalog import Catalog, Facet
from src.engine import biome, places, terrain
from src.models.world import Node, Planet
from src.units import METRES_PER_KM

#: The node property a found node carries: the id of its facet, named through
#: renames like every id of the world (D-251), beside its biome and province.
FACET = "facet"

#: The facet's own mosaic, offset from the height's seed and from the
#: field's other readings (`terrain.TEXTURE`, `terrain.STONES`) so that the
#: faces do not follow the hills or the woods.
FACET_SEED = terrain.STONES + 1
#: The axes run from nought to one; the middle is where a reading with
#: nothing to say lands -- a patch with no rise in it at all.
AXIS = (0, 1)
MIDDLE = globe.midpoint(*AXIS)


def axes(constants: Constants) -> dict[str, float]:
    """The axes the vault's boxes are written against (`biome.facet_axes`)."""
    return {key: float(value) for key, value in constants[R.BIOME_FACET_AXES].items()}


def readings(
    constants: Constants, planet: Planet, lat: float, lon: float
) -> tuple[float, float, float, float]:
    """The four numbers of a point: noise, slope, wet, high -- each in [0, 1]."""
    field = terrain.field_of(constants, planet)
    scale = axes(constants)
    radius = globe.radius_m(constants, planet)
    #: The mosaic: cells of `wave_m` metres. The lattice is counted to the
    #: radius, because that is what a cell of the noise measures -- a cell is
    #: `radius / cells` metres of arc, and a lattice counted round the whole
    #: circumference would have made the patches 2*pi times too small.
    cells = max(1.0, radius / scale["wave_m"])
    noise = relief.grain_at(field.seed + FACET_SEED, lat, lon, cells)

    #: The slope in metres per metre, read across a cell of the field: finer
    #: than that the raster only interpolates itself.
    step = field.step_m
    rise = field.relief_m
    patch = scale["patch_km"] * METRES_PER_KM
    #: Every point this reading needs, asked of the field **once**: four for
    #: the slope, one for the standpoint and a ring of them for the patch.
    #: Finding a cell of the equal-area grid costs the call and not the sums
    #: (`field.cells_at`), and thirteen calls a point told on the map.
    east = globe.offset(radius, (lat, lon), step, 0.0)
    west = globe.offset(radius, (lat, lon), -step, 0.0)
    north = globe.offset(radius, (lat, lon), 0.0, step)
    south = globe.offset(radius, (lat, lon), 0.0, -step)
    ring = [
        globe.offset(
            radius,
            (lat, lon),
            patch * math.sin(math.tau * point / globe.COMPASS_POINTS),
            patch * math.cos(math.tau * point / globe.COMPASS_POINTS),
        )
        for point in range(globe.COMPASS_POINTS)
    ]
    points = [(lat, lon), east, west, north, south, *ring]
    shares = field.reliefs(
        np.array([one[0] for one in points]), np.array([one[1] for one in points])
    )
    at_here, at_east, at_west, at_north, at_south = shares[: len(points) - len(ring)]
    #: The run is measured between the very points read, not reckoned from
    #: the step: the rise over the run is the slope, and both are metres.
    dx = (at_east - at_west) * rise / globe.distance_m(radius, west, east)
    dy = (at_north - at_south) * rise / globe.distance_m(radius, south, north)
    slope = min(1.0, math.hypot(dx, dy) / scale["slope_full"])

    #: The water: whichever is nearer, the fresh or the sea.
    reach = scale["wet_km"] * METRES_PER_KM
    to_water = min(field.river_distance_m(lat, lon), field.sea_distance_m(lat, lon))
    wet = max(0.0, 1.0 - to_water / reach) if reach > 0 else 0.0

    #: The patch: where this point stands among the ring about it.
    here = float(at_here)
    around = [float(one) for one in shares[len(points) - len(ring) :]]
    low, top = min([here, *around]), max([here, *around])
    high = (here - low) / (top - low) if top > low else MIDDLE
    return noise, slope, wet, high


def _distance(facet: Facet, slope: float, wet: float, high: float) -> float:
    """How far a point lies outside a facet's box: zero inside it."""
    return math.hypot(
        *(
            max(0.0, span[0] - value, value - span[1])
            for span, value in (
                (facet.where.slope, slope),
                (facet.where.wet, wet),
                (facet.where.high, high),
            )
        )
    )


def weights(
    rows: tuple[Facet, ...],
    slope: float,
    wet: float,
    high: float,
    soft: float,
    favours: tuple[str, ...] = (),
    favour_k: float = 1.0,
) -> list[float]:
    """What each facet is worth at this point: its share, faded by how far the
    point lies outside its box (`biome.facet_axes.soft_edge`). Inside the box
    the share stands whole, so where the boxes do hold the point the vault's
    shares are the odds; outside them the nearer boxes still divide the ground
    between themselves rather than the nearest one taking all of it.

    A province's favoured faces weigh `favour_k` times as much inside it
    (`favours` of `data/provinces.yaml`, plan §7): the Ore Ridge is known by
    its screes and rock faces, not by its label alone."""
    out = []
    for row in rows:
        away = _distance(row, slope, wet, high) / soft
        liked = favour_k if row.id in favours else 1.0
        out.append(row.share * liked * math.exp(-away * away))
    return out


def choose(
    rows: tuple[Facet, ...],
    grain: float,
    slope: float,
    wet: float,
    high: float,
    soft: float,
    favours: tuple[str, ...] = (),
    favour_k: float = 1.0,
) -> Facet | None:
    """The facet of these readings: the mosaic's flat draw divides the facets
    by what each is worth here (`weights`).

    Never nothing where the biome has faces at all: a node without one would
    be a hole in the map's words.
    """
    if not rows:
        return None
    weighed = weights(rows, slope, wet, high, soft, favours, favour_k)
    total = sum(weighed)
    if total <= 0:  # pragma: no cover -- every box is within reach of some point
        return rows[0]
    cut = min(max(grain, 0.0), 1.0) * total
    for row, weight in zip(rows, weighed, strict=True):
        cut -= weight
        if cut <= 0:
            return row
    return rows[-1]


def at(
    constants: Constants,
    catalog: Catalog,
    planet: Planet,
    lat: float,
    lon: float,
    here: str | None = None,
) -> Facet | None:
    """The facet at a point of the ground, or nothing where there is water or
    the vault has no facets for the biome. `here` is the biome if the caller
    has classified the point already."""
    where = here if here is not None else biome.classify(constants, planet, lat, lon)
    if where is None:
        return None
    rows = catalog.facets.of_biome(where)
    if not rows:
        return None
    scale = axes(constants)
    field = terrain.field_of(constants, planet)
    return choose(
        rows,
        *readings(constants, planet, lat, lon),
        scale["soft_edge"],
        field.province_favours_at(lat, lon),
        scale["favour_k"],
    )


def of_node(constants: Constants, catalog: Catalog, node: Node) -> Facet | None:
    """The facet written on a found node, or the one its point wears: as the
    biome is read (`biome.of_node`), so nothing is invented for a seeded node.

    A found node answers off its own properties and costs nothing; a seeded
    one reads the field, which is a few milliseconds. Ask it per node in a
    loop and that is the loop's price -- the map asks it once, for the node
    under the body.
    """
    written = (node.properties or {}).get(FACET)
    if written:
        return catalog.facets.by_id(str(written))
    point = places.geo_of(node)
    if point is None:
        return None
    return at(constants, catalog, node.planet, *point)


def of_node_id(node: Node) -> str | None:
    """The facet id written on a found node, or nothing: a seeded node and a
    node off the ground have none, as with the province."""
    written = (node.properties or {}).get(FACET)
    return str(written) if written else None


def marks(constants: Constants, here: str, facet: Facet | None) -> dict[str, float]:
    """The shares of woods, stones and meadow of a place: the facet's own
    where it has them, the biome's otherwise (per cent)."""
    if facet is not None:
        return dict(facet.marks)
    return biome.marks(constants, here)


def vein_k(constants: Constants, here: str, facet: Facet | None) -> float:
    """How much likelier a vein is here: the biome's, times the facet's."""
    return biome.vein_k(constants, here) * (facet.vein_k if facet else 1.0)


def swing_c(constants: Constants, here: str, facet: Facet | None) -> float:
    """The day's swing at a place: the biome's, times the facet's."""
    return biome.swing_c(constants, here) * (facet.swing_k if facet else 1.0)


def room_floor(constants: Constants) -> float:
    """The shortest aim the ground itself allows, metres: a find of the least
    area may not overlap one of the greatest (D-321 item 4), so a node whose
    far reach fell under this could aim at nothing at all -- every target
    inside the band would be refused for want of room, every one beyond it as
    too far, and the scout would stand there for good.

    All of it is the vault's: the area a find takes and the share of the free
    radius it fills (`explore.node_area`, `explore.fill_share`).
    """
    span = constants[R.EXPLORE_NODE_AREA]
    fill = float(constants[R.EXPLORE_FILL_SHARE])
    return globe.radius_of_area(span.min) / fill + globe.radius_of_area(span.max)


def reach_m(constants: Constants, here: str, facet: Facet | None) -> tuple[float, float]:
    """How near and how far one may scout from a place: the biome's band,
    times the facet's -- one sees further from a bald knoll than from a
    thicket, and the vault says by how much.

    A face may narrow the band but never close it: ten of the vault's faces
    (the reeds at 0.4, the thickets at 0.6) would shorten a twenty-metre
    reach below the room a node needs, and the find would be a dead end. The
    floor is the placement rule's own (`room_floor`), not a number of this
    module's.
    """
    near, far = biome.reach_m(constants, here)
    k = facet.reach_k if facet else 1.0
    return near * k, max(far * k, room_floor(constants))
