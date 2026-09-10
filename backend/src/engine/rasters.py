# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The rasters a planet's picture is drawn from (landscape plan wave 5, §9.3).

The arrays the client reads: the height in signed metres, sixteen bits a
cell; the biome as a byte into `biome.names`; the landform as a byte into
the field's table; the water as a byte into `field.WATER_NAMES` -- the
rivers are cells of it, which the vector layer threads (wave 6); the rock's
hardness, a byte the shader's grain takes its character from (wave 8); the
province, the distance to fresh water, the catchment of a river and the
lakes as a quantity. Written once per field and kept: a constant of the
vault must not be encoded on every request. The shader draws by them and
judges nothing -- land, aim and find stay the server's, off the field
itself (plan §9.1).

Since D-328 the field is HEALPix, and so is the picture: twelve square
faces of `nside` cells a side, laid out four across and three down as one
texture. The layout is not a convenience -- it is why the grid was chosen
over an icosphere: a face stays a square, so the picture is still a texture
and the blending between cells inside a face is the hardware's, free.

Every face carries a **border** of one cell taken from the face across the
edge, because that blending would otherwise reach into the neighbouring
tile of the atlas and drag a strip of the wrong face along all twelve
seams. The border cell is found by geometry -- the cell reflected outwards
past the edge -- so the picture is continuous over the whole sphere and the
shader needs to know nothing about which face adjoins which.
"""

from __future__ import annotations

import math

import numpy as np

from src import field as fields
from src import healpix
from src.constants import Constants
from src.engine import biome, terrain
from src.models.world import Planet
from src.runtime import RIVER_PAINT_M

#: How far off nought a coast texel of the picture is held on the side its
#: own class stands (`_shore`), as a share of the rise -- the sketch's own.
SHORE = fields.RASTER_SHORE
#: The atlas is the grid's own layout, and it is read from there: the
#: shader finds its texel by the same numbers (`shade.ts`).
ACROSS, DOWN, BORDER = healpix.ACROSS, healpix.DOWN, healpix.BORDER


def raster_bytes(constants: Constants, planet: Planet, kind: str) -> bytes | None:
    """One raster as bytes, the atlas row by row, little-endian; None for a
    kind that is not one."""
    if kind not in terrain.RASTER_KINDS:
        return None
    field = terrain.field_of(constants, planet)
    key = (id(field), kind)
    got = _BYTES.get(key)
    if got is None:
        got = _encode(constants, planet, field, kind)
        _BYTES[key] = got
    return got


def _thin(field, values: np.ndarray, nside: int, average: bool) -> np.ndarray:
    """The field's cells at a coarser fineness.

    Both grids are the same kind, so a coarse cell is asked of the
    projection and not of the indices: the field's cells whose middles fall
    inside it are its own, however the two finenesses stand to one another.

    A quantity is the mean of what falls in, a class the field's cell at the
    coarse cell's own middle: an average of codes is not a code. That leaves
    a coast cell of the picture with a height averaged across the water's
    edge and a class taken from its middle, which can disagree -- see
    `_shore`, which puts the sign back where the class says it belongs.
    """
    if nside == field.nside:
        return values
    if average:
        lat, lon = field.centres
        home = healpix.ang2pix(nside, lat, lon)
        many = healpix.npix(nside)
        total = np.bincount(home, weights=values.astype(np.float64), minlength=many)
        return total / np.maximum(np.bincount(home, minlength=many), 1)
    lat, lon = healpix.centres(nside)
    return values[healpix.ang2pix(field.nside, lat, lon)]


def _shore(field, height: np.ndarray, nside: int) -> np.ndarray:
    """The thinned height with the sign its own cell's class asks for.

    The picture tells water from land by the sign of the height, and colours
    the land by the class at the same texel. Thinned apart, the two can
    disagree on a coast: two cells of sea and two of land average to a
    height above nought while the class in the middle says sea, and the
    picture draws a strip of shore where the field has water. Held to the
    class, as the sketch holds its own coarse grid (`field._sketch`).
    """
    if nside == field.nside:
        return height
    wet = _thin(field, field.water, nside, False) == fields.SEA
    return np.where(wet, np.minimum(height, -SHORE), np.maximum(height, SHORE))


def _skirt_of(field, nside: int) -> np.ndarray:
    """The atlas layout of a fineness, made once.

    Kept by fineness and not by planet: Terra and Aquatica are the same grid,
    and the layout of a grid is the grid's, not the world's.
    """
    skirt = _SKIRTS.get(nside)
    if skirt is None:
        #: The field's own ring table when the picture is at its fineness --
        #: building a second one is a million lookups and a third of a second.
        skirt = healpix.skirt(nside, field.rings if nside == field.nside else None)
        _SKIRTS[nside] = skirt
    return skirt


def _laid(field, values: np.ndarray, nside: int, average: bool) -> np.ndarray:
    """A raster thinned to `nside` and laid out as the atlas."""
    return _thin(field, values, nside, average)[_skirt_of(field, nside)]


def _laid_height(field, nside: int) -> np.ndarray:
    """The height as the atlas keeps it: thinned, held to its class's side
    of the water's edge, then laid out."""
    thinned = _thin(field, field.height.astype(np.float64), nside, True)
    return _shore(field, thinned, nside)[_skirt_of(field, nside)]


def _encode(constants: Constants, planet: Planet, field, kind: str) -> bytes:
    nside = terrain.raster_nside(field)

    def laid(values: np.ndarray, average: bool = False) -> np.ndarray:
        return _laid(field, values, nside, average)

    if kind == "height":
        share = _laid_height(field, nside)
        metres = np.round(share * field.relief_m)
        #: The sea stays under zero: the picture tells the water by the sign
        #: of the height, and a shallow cell rounded up to nought would be
        #: drawn as shore.
        metres = np.where(share < 0, np.minimum(metres, -1), metres)
        short = np.iinfo(np.int16)
        return np.clip(metres, short.min, short.max).astype("<i2").tobytes()
    if kind == "biome":
        return _bytes(laid(biome.raster(constants, planet)))
    if kind == "water":
        return _bytes(laid(field.water))
    if kind == "rock":
        return _bytes(laid(field.hardness))
    if kind == "province":
        return _bytes(laid(field.province))
    if kind == "river":
        #: How far the nearest fresh water lies, a metre a step. A byte
        #: reaches further than the widest river is (`flow_bank`), and past
        #: that the picture has no use for the number.
        byte = np.iinfo(np.uint8)
        return _bytes(np.clip(laid(field.river_m.astype(np.float64), average=True), 0, byte.max))
    if kind == "lake":
        #: A lake as a quantity rather than as a class, so the picture can
        #: cut its shore between the cells as it cuts the sea's by the
        #: height. Read as a class it was a lake of whole cells -- blue
        #: rectangles with right angles, which is not a lake.
        byte = np.iinfo(np.uint8)
        wet = (field.water == fields.LAKE).astype(np.float64) * byte.max
        return _bytes(laid(wet, average=True))
    if kind == "stream":
        #: A river as a **quantity**, so the picture can cut its bank between
        #: the cells (owner, 2026-09-11: rivers as a raster, the vectors look
        #: bad). But not as the share of the cell that is river, which is how
        #: this began: a river is one cell wide, and a share read between the
        #: cells and cut at a half falls apart on every diagonal step -- the
        #: midpoint between two diagonal neighbours averages two wet corners
        #: against two dry ones and lands under the knife. The ribbon came out
        #: a string of beads.
        #:
        #: A **distance** does not do that. `river_m` is the metres to the
        #: nearest fresh water, and it is smooth by construction, so its level
        #: sets are curves rather than crumbs. What travels is the ramp
        #: `1 - river_m / RIVER_PAINT_M`, and the shader cuts it at a half:
        #: a ribbon `RIVER_PAINT_M / 2` metres wide that bends where the water
        #: bends. Lakes and the sea are excluded -- they have their own paint,
        #: and near a lake shore this would otherwise draw a river.
        byte = np.iinfo(np.uint8)
        fresh = (field.water == fields.LAKE) | (field.water == fields.SEA)
        far = np.where(fresh, RIVER_PAINT_M, field.river_m.astype(np.float64))
        ramp = np.clip(1.0 - far / RIVER_PAINT_M, 0.0, 1.0) * byte.max
        return _bytes(laid(ramp, average=True))
    if kind == "flow":
        #: The catchment on a log scale, because a river's catchment runs
        #: from twenty square kilometres to four thousand and the eye reads
        #: the small ones as often as the great.
        byte = np.iinfo(np.uint8)
        top = float(field.river_flow_km2.max())
        share = (
            np.log1p(field.river_flow_km2) / math.log1p(top) if top > 0 else np.zeros(field.cells)
        )
        return _bytes(np.clip(laid(share, average=True) * byte.max, byte.min, byte.max))
    return _bytes(laid(field.form))


def _bytes(values: np.ndarray) -> bytes:
    return np.ascontiguousarray(values).astype(np.uint8).tobytes()


#: Rasters by field and kind, and the atlas layout by field and fineness;
#: the fields live for the process (`field.of`), so their ids are stable keys.
_BYTES: dict[tuple[int, str], bytes] = {}
_SKIRTS: dict[int, np.ndarray] = {}


def warm(constants: Constants) -> None:
    """Cut every planet's rasters now.

    They are a constant of the vault and are made once; the question is only
    where the making is paid for. Made on demand it is most of a second of
    arithmetic inside a public route, and the route is `async def` on the
    loop, so the first reader of a planet's picture stops every other
    session in the process. Startup already declares that price for the
    fields themselves (`field.preload`), and this belongs beside it.
    """
    for planet in Planet:
        for kind in terrain.RASTER_KINDS:
            raster_bytes(constants, planet, kind)
    #: The layout was scaffolding for the cutting and nobody reads it again.
    _SKIRTS.clear()
