# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Where a point of the sphere falls on the planet's grid (D-328).

The vault cuts a planet into `12 nside²` cells of **equal area** and writes
the field cell by cell; this is the half of that grid the game needs: given
a latitude and a longitude, which cell, and -- for the quantities, where a
staircase of cells would show -- which four cells and in what proportions.

Why the projection lives here as well as in the vault: the two repositories
cannot import one another, and a grid is not data that can travel in a file
-- the file is cells, and finding one's cell is arithmetic. The same
arithmetic therefore exists twice, and the two could drift apart silently:
a world read a kilometre off looks like a world, and nothing falls over. So
the vault writes a handful of points and the cells it put them in into the
passport, and `field` checks them against this module when it reads the
file (`field._check_probe`). Drift stops the server rather than moving the
ground.

Only the forward projection is written out. The rings -- the belts of equal
latitude the cells lie in, which is what HEALPix has and an icosphere has
not -- are found by asking `ang2pix` for their own middles, so there is one
projection here and not two that could disagree with each other.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cached_property

import numpy as np

#: Where the equatorial belt gives way to the polar caps, by the sine of
#: the latitude. Above it the cells are cut by Collignon's projection,
#: below by Lambert's; the two agree on the line, so there is no seam.
POLAR_Z = 2.0 / 3.0
#: A quarter turn: HEALPix reckons longitude in them.
QUARTER = math.pi / 2.0
#: How the twelve faces are laid out as one texture, and how wide a border
#: of the face over the edge each carries. The layout is a fact of the grid,
#: not of the encoder: the shader finds a texel by it (`shade.ts`), the
#: server writes one by it (`engine.rasters`), and both take it from here.
FACES = 12
ACROSS, DOWN = 4, 3
BORDER = 1
#: Two of a thing: a border on each side of a face, the pair of cells a
#: reflection steps over, the middle of a block. Geometry, not a number of
#: the world.
BOTH = 2


def tile_shape(nside: int) -> tuple[int, int]:
    """The atlas: how many texels down and across, borders counted."""
    side = nside + 2 * BORDER
    return DOWN * side, ACROSS * side


#: How near a cell's own middle a point has to be to read as that cell and
#: nothing else (see `Rings.between`). A part in a thousand million of a
#: cell is four ten-thousandths of a millimetre of Terra's ground.
OWN_CELL = 1e-9


def npix(nside: int) -> int:
    """How many cells a planet of this fineness has."""
    return FACES * int(nside) * int(nside)


def cell_side_m(radius_m: float, nside: int) -> float:
    """The side of a cell, metres -- one number for the whole planet."""
    return math.sqrt(4.0 * math.pi * radius_m * radius_m / npix(nside))


def ang2pix(nside: int, lat_deg: np.ndarray, lon_deg: np.ndarray) -> np.ndarray:
    """The cell a point falls in. Latitude and longitude in degrees, and
    they broadcast: a column of latitudes against a row of longitudes is a
    lattice of points."""
    nside = int(nside)
    z, phi = np.broadcast_arrays(
        np.sin(np.radians(np.asarray(lat_deg, dtype=np.float64))),
        np.radians(np.asarray(lon_deg, dtype=np.float64)) % (2.0 * math.pi),
    )
    z = np.clip(z, -1.0, 1.0)
    za = np.abs(z)
    turns = phi / QUARTER

    face = np.zeros(z.shape, dtype=np.int64)
    ix = np.zeros(z.shape, dtype=np.int64)
    iy = np.zeros(z.shape, dtype=np.int64)

    #: The belt: two families of slanting lines, rising and falling; which
    #: side of them the point lies on says which face it is on.
    belt = za <= POLAR_Z
    if belt.any():
        first = nside * (0.5 + turns[belt])
        second = nside * (z[belt] * 0.75)
        up = np.floor(first - second).astype(np.int64)
        down = np.floor(first + second).astype(np.int64)
        same = up // nside == down // nside
        north = up // nside < down // nside
        face[belt] = np.where(
            same, (up // nside & 3) + 4, np.where(north, up // nside & 3, (down // nside & 3) + 8)
        )
        ix[belt] = down % nside
        iy[belt] = nside - (up % nside) - 1

    #: The caps: the point goes into the Collignon diamond of its quarter.
    cap = ~belt
    if cap.any():
        quarter = np.minimum(3, turns[cap].astype(np.int64))
        along = turns[cap] - quarter
        #: Nothing under the root at the pole itself: there the whole
        #: quarter is one cell.
        reach = nside * np.sqrt(np.maximum(0.0, 3.0 * (1.0 - za[cap])))
        up = np.minimum((along * reach).astype(np.int64), nside - 1)
        down = np.minimum(((1.0 - along) * reach).astype(np.int64), nside - 1)
        top = z[cap] >= 0
        face[cap] = np.where(top, quarter, quarter + 8)
        ix[cap] = np.where(top, nside - down - 1, up)
        iy[cap] = np.where(top, nside - up - 1, down)
    return (face * nside + iy) * nside + ix


def ring_of(nside: int, lat_deg: np.ndarray) -> np.ndarray:
    """Where a latitude stands among the rings, as a fraction.

    Whole numbers are the middles of rings, counted from the north; the
    ring a cell belongs to is what makes the belts of equal latitude, and
    reading a quantity between the rings is what keeps a coast a line
    rather than a staircase of cells.
    """
    nside = int(nside)
    z = np.clip(np.sin(np.radians(np.asarray(lat_deg, dtype=np.float64))), -1.0, 1.0)
    belt = 2.0 * nside - 1.5 * nside * z
    north = nside * np.sqrt(np.maximum(0.0, 3.0 * (1.0 - z)))
    south = 4.0 * nside - nside * np.sqrt(np.maximum(0.0, 3.0 * (1.0 + z)))
    return np.where(np.abs(z) <= POLAR_Z, belt, np.where(z > 0, north, south))


def ring_shape(nside: int, ring: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """How many cells a ring holds (as a quarter of them) and whether it is
    the half-step-shifted kind."""
    nside = int(nside)
    ring = np.asarray(ring)
    quarter = np.where(ring < nside, ring, np.where(ring > 3 * nside, 4 * nside - ring, nside))
    shifted = np.where((ring < nside) | (ring > 3 * nside), 0, (ring - nside) & 1)
    return quarter, shifted


def ring_lat(nside: int, ring: np.ndarray) -> np.ndarray:
    """The latitude of a ring's cells, degrees."""
    nside = int(nside)
    ring = np.asarray(ring, dtype=np.float64)
    three = 3.0 * nside * nside
    z = np.where(
        ring < nside,
        1.0 - ring * ring / three,
        np.where(
            ring > 3 * nside,
            (4.0 * nside - ring) ** 2 / three - 1.0,
            (2.0 * nside - ring) * 2.0 / (3.0 * nside),
        ),
    )
    return np.degrees(np.arcsin(np.clip(z, -1.0, 1.0)))


def ring_lon(quarter: np.ndarray, shifted: np.ndarray, place: np.ndarray) -> np.ndarray:
    """The longitude of a place in a ring, degrees. Places run east and wrap."""
    phi = (np.asarray(place) + 0.5 - np.asarray(shifted) / 2.0) * (QUARTER / np.asarray(quarter))
    return ((np.degrees(phi) + 180.0) % 360.0) - 180.0


@dataclass(frozen=True)
class Rings:
    """The cells of a planet laid out by ring and by place along it.

    Built by asking `ang2pix` for the middle of every place of every ring,
    so there is one projection in this module and not two. Costs a million
    lookups once per planet and saves the game from carrying a table of
    neighbours it has no other use for.
    """

    nside: int

    @property
    def count(self) -> int:
        return 4 * self.nside - 1

    @property
    def wide(self) -> int:
        return 4 * self.nside

    @cached_property
    def table(self) -> np.ndarray:
        """`(rings, 4 nside)` cells: place `p` of a short ring repeats every
        `4 nr`, so a place past the ring's end is that ring come round."""
        ring = np.arange(1, self.count + 1)
        quarter, shifted = ring_shape(self.nside, ring)
        place = np.arange(self.wide)[None, :] % (4 * quarter)[:, None]
        lat = ring_lat(self.nside, ring)[:, None]
        lon = ring_lon(quarter[:, None], shifted[:, None], place)
        return ang2pix(self.nside, lat, lon).astype(np.int32)

    def corners(self, lat_deg: np.ndarray, lon_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The four cells around a point and their shares, both `(4, ...)`.

        Two rings and two places in each: the bilinear reading HEALPix has
        instead of the four corners of a square, and the reason a shore is
        a line through the cells rather than their staircase.
        """
        nside = self.nside
        fraction = np.clip(ring_of(nside, lat_deg), 1.0, float(self.count))
        low = np.minimum(np.floor(fraction).astype(np.int64), self.count - 1)
        down = fraction - low
        phi = np.radians(np.asarray(lon_deg, dtype=np.float64)) % (2.0 * math.pi)
        cells = []
        shares = []
        for ring, weight in ((low, 1.0 - down), (low + 1, down)):
            quarter, shifted = ring_shape(nside, ring)
            along = phi * (2.0 * quarter / math.pi) - 0.5 + shifted / 2.0
            first = np.floor(along)
            across = along - first
            for place, share in ((first, 1.0 - across), (first + 1.0, across)):
                seat = np.mod(place.astype(np.int64), 4 * quarter)
                cells.append(self.table[ring - 1, seat])
                shares.append(weight * share)
        return np.stack(cells), np.stack(shares)

    def between(self, raster: np.ndarray, lat_deg: np.ndarray, lon_deg: np.ndarray) -> np.ndarray:
        """A quantity read between the cells around a point.

        At a cell's own middle the answer is that cell's value and nothing
        else. That is what makes a reading an interpolation rather than a
        blur, and here it has to be said out loud: on a square grid the
        weights fall out of the same lattice the middle is given in and are
        exactly one and nought; here the middle goes out through a sine and
        comes back through an arc-sine, and a part in a hundred thousand
        million of the next cell rides back with it. Nothing that matters
        for a height -- but the classifier's bands stand on whole degrees
        and the field's temperature is whole degrees, so a cell at exactly
        five degrees is read as four-point-nine-nine-nine and sorted into
        the band below, and the picture and the find then disagree about
        real ground.
        """
        cells, shares = self.corners(lat_deg, lon_deg)
        values = raster[cells].astype(np.float64)
        top = shares.argmax(axis=0)[None, ...]
        own = np.take_along_axis(values, top, 0)[0]
        return np.where(
            np.take_along_axis(shares, top, 0)[0] >= 1.0 - OWN_CELL,
            own,
            (values * shares).sum(axis=0),
        )


def pix2fxy(nside: int, pix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A cell as face and place within it. Cells are kept face by face, and
    a face stays a square -- that is what lets the picture hold the field as
    a texture at all (D-328)."""
    nside = int(nside)
    face, rest = np.divmod(np.asarray(pix, dtype=np.int64), nside * nside)
    iy, ix = np.divmod(rest, nside)
    return face, ix, iy


def fxy2pix(nside: int, face: np.ndarray, ix: np.ndarray, iy: np.ndarray) -> np.ndarray:
    """The other way: face and place to cell."""
    nside = int(nside)
    return (np.asarray(face) * nside + np.asarray(iy)) * nside + np.asarray(ix)


def centres(nside: int, rings: Rings | None = None) -> tuple[np.ndarray, np.ndarray]:
    """The middle of every cell of a grid this fine, degrees.

    The ring table may be handed in by a caller that already has one -- it
    is a million lookups to build and the field keeps its own.
    """
    rings = rings or Rings(nside)
    ring = np.arange(1, rings.count + 1)
    quarter, shifted = ring_shape(nside, ring)
    latitude = ring_lat(nside, ring)
    lat = np.empty(npix(nside))
    lon = np.empty(npix(nside))
    for index in range(rings.count):
        wide = int(4 * quarter[index])
        cells = rings.table[index, :wide]
        lat[cells] = latitude[index]
        lon[cells] = ring_lon(quarter[index], shifted[index], np.arange(wide))
    return lat, lon


def xyz(lat_deg: np.ndarray, lon_deg: np.ndarray) -> np.ndarray:
    """Points of the unit ball, the three coordinates first."""
    rad, lam = np.radians(lat_deg), np.radians(lon_deg)
    return np.stack([np.cos(rad) * np.cos(lam), np.cos(rad) * np.sin(lam), np.sin(rad)])


def skirt(nside: int, rings: Rings | None = None) -> np.ndarray:
    """For every texel of the atlas, the cell whose value goes in it.

    Inside a face it is that face's own cell. In the border it is the cell
    one step past the edge, found by reflecting the edge cell outwards over
    its inward neighbour -- the point at twice the angle along the same
    great circle -- and asking the projection whose cell that is. Whatever
    face lies across the edge, and whichever way round its own lattice runs,
    the answer is right by construction; at the eight corners where three
    faces meet the reflection lands a fraction of a cell off, which is a
    corner texel of a border and is never the middle of anything.

    Without the border, the blending between neighbouring cells would reach
    into the next tile of the atlas at every face edge and drag a strip of a
    stranger's ground along all twelve seams.
    """
    side = nside + BOTH * BORDER
    place = np.arange(-BORDER, nside + BORDER)
    ix = np.clip(place, 0, nside - 1)
    iy = np.clip(place, 0, nside - 1)
    #: Which way, if any, this row and column step outward past the edge.
    out_x = np.sign(place - ix)
    out_y = np.sign(place - iy)
    face = np.arange(FACES)[:, None, None]
    here = fxy2pix(nside, face, ix[None, None, :], iy[None, :, None])
    inward = fxy2pix(
        nside,
        face,
        np.clip(ix - out_x, 0, nside - 1)[None, None, :],
        np.clip(iy - out_y, 0, nside - 1)[None, :, None],
    )
    lat, lon = centres(nside, rings)
    edge = xyz(lat[here], lon[here])
    back = xyz(lat[inward], lon[inward])
    beyond = float(BOTH) * (edge * back).sum(axis=0)[None, ...] * edge - back
    outside = (out_x[None, None, :] != 0) | (out_y[None, :, None] != 0)
    over = ang2pix(
        nside,
        np.degrees(np.arcsin(np.clip(beyond[BOTH], -1.0, 1.0))),
        np.degrees(np.arctan2(beyond[1], beyond[0])),
    )
    cells = np.where(outside, over, here)
    out = np.zeros((DOWN * side, ACROSS * side), dtype=np.int32)
    for one in range(FACES):
        top, left = (one // ACROSS) * side, (one % ACROSS) * side
        out[top : top + side, left : left + side] = cells[one]
    return out
