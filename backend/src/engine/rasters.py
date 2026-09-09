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

    A quantity is the mean of the cells it swallows, a class the cell in the
    middle of them: an average of codes is not a code. The coarse fineness
    always divides the field's own (`terrain.raster_nside`), so a coarse cell
    is a square block of fine ones inside one face and nothing crosses a seam.
    """
    if nside == field.nside:
        return values
    factor = field.nside // nside
    face, ix, iy = healpix.pix2fxy(field.nside, np.arange(field.cells))
    if average:
        home = healpix.fxy2pix(nside, face, ix // factor, iy // factor)
        total = np.bincount(home, weights=values.astype(np.float64), minlength=healpix.npix(nside))
        return total / float(factor * factor)
    face, ix, iy = healpix.pix2fxy(nside, np.arange(healpix.npix(nside)))
    middle = factor // healpix.BOTH
    return values[healpix.fxy2pix(field.nside, face, ix * factor + middle, iy * factor + middle)]


def _laid(field, values: np.ndarray, nside: int, average: bool) -> np.ndarray:
    """A raster thinned to `nside` and laid out as the atlas."""
    key = (id(field), nside)
    skirt = _SKIRTS.get(key)
    if skirt is None:
        skirt = healpix.skirt(nside)
        _SKIRTS[key] = skirt
    return _thin(field, values, nside, average)[skirt]


def _encode(constants: Constants, planet: Planet, field, kind: str) -> bytes:
    nside = terrain.raster_nside(field)

    def laid(values: np.ndarray, average: bool = False) -> np.ndarray:
        return _laid(field, values, nside, average)

    if kind == "height":
        share = laid(field.height.astype(np.float64), average=True)
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
_SKIRTS: dict[tuple[int, int], np.ndarray] = {}
