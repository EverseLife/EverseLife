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

Every face carries a **border** of `healpix.BORDER` cells taken from the
face across the edge, because that blending would otherwise reach into the
neighbouring tile of the atlas and drag a strip of the wrong face along all
twelve seams. The border cells are found by geometry -- the edge cell
carried outwards past the edge -- so the picture is continuous over the
whole sphere and the shader needs to know nothing about which face adjoins
which.
"""

from __future__ import annotations

import math

import numpy as np

from src import field as fields
from src import healpix
from src.constants import Constants
from src.constants import registry as R
from src.engine import biome, terrain
from src.models.world import Planet

#: How far off nought a coast texel of the picture is held on the side its
#: own class stands (`_shore`), as a share of the rise -- the sketch's own.
SHORE = fields.RASTER_SHORE
#: The atlas is the grid's own layout, and it is read from there: the
#: shader finds its texel by the same numbers (`shade.ts`).
ACROSS, DOWN, BORDER = healpix.ACROSS, healpix.DOWN, healpix.BORDER
#: What one step of the height raster is worth, metres. A whole metre was
#: too coarse for the shore: the land there stands a few decimetres over
#: the sea, and rounded to nought it made a flat plateau of cells at
#: exactly zero -- the water's edge then ran along the middles of texels,
#: which is the lattice, and the coast was straight runs at forty-five
#: degrees with corners (owner, 2026-09-11: diamonds on the map). A
#: decimetre keeps the shore's own slope; sixteen bits of them reach three
#: kilometres either way, past any rise the registry names. The passport
#: carries the unit (`height_unit_m`, from `field` -- this module and the
#: passport's cannot import one another), and the picture divides by it.
HEIGHT_UNIT_M = fields.HEIGHT_UNIT_M


def raster_bytes(
    constants: Constants, planet: Planet, kind: str, nside: int | None = None
) -> bytes | None:
    """One raster as bytes, the atlas row by row, little-endian, at the
    picture's own fineness or at another it is served at
    (`terrain.picture_nsides`: the preview's, for the shader's rasters
    alone); None for a kind that is not one, or not kept at that fineness."""
    field = terrain.field_of(constants, planet)
    fineness = terrain.raster_nside(field) if nside is None else nside
    if kind not in terrain.kinds_at(field, fineness):
        return None
    key = (id(field), kind, fineness)
    got = _BYTES.get(key)
    if got is None:
        got = _encode(constants, planet, field, kind, fineness)
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
        home, count = _homes(field, nside)
        total = np.bincount(home, weights=values.astype(np.float64), minlength=count.size)
        return total / np.maximum(count, 1)
    return values[_middles(field, nside)]


def _homes(field, nside: int) -> tuple[np.ndarray, np.ndarray]:
    """Which coarse cell each of the field's cells falls in, and how many
    fall in each: the projection of a million middles, asked once per
    fineness rather than once per raster -- a dozen rasters at two
    finenesses asked it two dozen times over, and it was most of the
    seconds the picture's cutting took at startup (2026-09-18)."""
    key = (id(field), nside)
    got = _HOMES.get(key)
    if got is None:
        lat, lon = field.centres
        home = healpix.ang2pix(nside, lat, lon)
        got = (home, np.bincount(home, minlength=healpix.npix(nside)))
        _HOMES[key] = got
    return got


def _middles(field, nside: int) -> np.ndarray:
    """The field's cell under the middle of each coarse cell, once per
    fineness for the same reason (`_homes`)."""
    key = (id(field), nside)
    got = _MIDDLES.get(key)
    if got is None:
        lat, lon = healpix.centres(nside)
        got = healpix.ang2pix(field.nside, lat, lon)
        _MIDDLES[key] = got
    return got


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


def _encode(constants: Constants, planet: Planet, field, kind: str, nside: int) -> bytes:
    def laid(values: np.ndarray, average: bool = False) -> np.ndarray:
        return _laid(field, values, nside, average)

    if kind == "height":
        share = _laid_height(field, nside)
        steps = np.round(share * field.relief_m / HEIGHT_UNIT_M)
        #: The sea stays under zero: the picture tells the water by the sign
        #: of the height, and a shallow cell rounded up to nought would be
        #: drawn as shore.
        steps = np.where(share < 0, np.minimum(steps, -1), steps)
        short = np.iinfo(np.int16)
        #: A rise the steps cannot count would be clipped to a plateau in
        #: silence; the registry's rises are a tenth of the reach.
        if field.relief_m / HEIGHT_UNIT_M > short.max:
            raise ValueError(
                f"relief {field.relief_m} m does not fit int16 steps of {HEIGHT_UNIT_M} m"
            )
        return np.clip(steps, short.min, short.max).astype("<i2").tobytes()
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
        #: The river's ribbon, straight out of the field: its width is the
        #: flow of the river it belongs to, and the width was decided where
        #: the flow lives (`field.pipeline`). Read as a class a river is a
        #: chain of whole cells with right angles; as a share it fell apart
        #: on every diagonal. As a ribbon it bends where the water bends.
        #:
        #: Held to the class, as the height is (`_shore`): the ribbon is a
        #: quantity and is thinned by the mean, the water a class and thinned
        #: by the middle, and a texel of the channel whose mean took in a
        #: cell beyond the bank read as dry ground where the class said
        #: river. A channel texel is never under its own middle cell's value.
        byte = np.iinfo(np.uint8)
        ribbon = field.stream.astype(np.float64)
        mean = laid(ribbon, average=True)
        own = laid(ribbon)
        channel = laid(field.water) == fields.RIVER
        return _bytes(np.clip(np.where(channel, np.maximum(mean, own), mean), 0, byte.max))
    if kind == "temperature":
        #: The climate as the map's climate layer reads it (D-331): the
        #: mean over the thinned cells, in the field's half-degree steps.
        byte = np.iinfo(np.uint8)
        warm = laid(field.temperature_c.astype(np.float64), average=True)
        span = constants[R.TERRAIN_TEMP_RANGE][planet.value]
        floor, step = fields.temperature_scale(span["min"], span["max"])
        return _bytes(np.clip(np.round((warm - floor) / step), byte.min, byte.max))
    if kind == "rain":
        return _bytes(laid(field.rain.astype(np.float64), average=True))
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


#: Rasters by field, kind and fineness, and the atlas layout by fineness;
#: the fields live for the process (`field.of`), so their ids are stable keys.
_BYTES: dict[tuple[int, str, int], bytes] = {}
_SKIRTS: dict[int, np.ndarray] = {}
#: The thinning's two projections by field and fineness (`_homes`,
#: `_middles`): scaffolding like the layout, let go once the cutting is done.
_HOMES: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
_MIDDLES: dict[tuple[int, int], np.ndarray] = {}


def served(constants: Constants, planet: Planet) -> list[tuple[int, str]]:
    """Every raster of a planet's picture that is served: the fineness and
    the kind, the picture's own first."""
    field = terrain.field_of(constants, planet)
    return [
        (nside, kind)
        for nside in terrain.picture_nsides(field)
        for kind in terrain.kinds_at(field, nside)
    ]


def warm(constants: Constants) -> None:
    """Cut every planet's rasters now, at every fineness they are served at.

    They are a constant of the vault and are made once; the question is only
    where the making is paid for. Made on demand it is most of a second of
    arithmetic inside a public route, and the route is `async def` on the
    loop, so the first reader of a planet's picture stops every other
    session in the process. Startup already declares that price for the
    fields themselves (`field.preload`), and this belongs beside it.
    """
    for planet in Planet:
        for nside, kind in served(constants, planet):
            raster_bytes(constants, planet, kind, nside)
    #: The layout and the projections were scaffolding for the cutting and
    #: nobody reads them again.
    _SKIRTS.clear()
    _HOMES.clear()
    _MIDDLES.clear()
