# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The rasters a planet's picture is drawn from (landscape plan wave 5, §9.3).

Five arrays the client reads: the height in signed metres, sixteen bits a
cell; the biome as a byte into `biome.names`; the landform as a byte into
the field's table; the water as a byte into `field.WATER_NAMES` -- the
rivers are cells of it, which the vector layer threads (wave 6); and the
rock's hardness, a byte the shader's grain takes its character from (wave
8). Thinned to `runtime.RASTER_ROWS_MAX`
rows, written once per field and kept: a constant of the vault must not be
encoded on every request. The shader draws by them and judges nothing --
land, aim and find stay the server's, off the field itself (plan §9.1).
"""

from __future__ import annotations

import math

import numpy as np

from src import field as fields
from src.constants import Constants
from src.engine import biome, terrain
from src.models.world import Planet


def raster_bytes(constants: Constants, planet: Planet, kind: str) -> bytes | None:
    """One raster as bytes, row 0 the south, little-endian; None for a kind
    that is not one."""
    if kind not in terrain.RASTER_KINDS:
        return None
    field = terrain.field_of(constants, planet)
    key = (id(field), kind)
    got = _BYTES.get(key)
    if got is None:
        got = _encode(constants, planet, field, kind)
        _BYTES[key] = got
    return got


def _encode(constants: Constants, planet: Planet, field, kind: str) -> bytes:
    stride = terrain.raster_stride(field.rows)
    if kind == "height":
        share = field.height[::stride, ::stride].astype(np.float64)
        metres = np.round(share * field.relief_m)
        #: The sea stays under zero: the picture tells the water by the sign
        #: of the height, and a shallow cell rounded up to nought would be
        #: drawn as shore.
        metres = np.where(share < 0, np.minimum(metres, -1), metres)
        short = np.iinfo(np.int16)
        return np.clip(metres, short.min, short.max).astype("<i2").tobytes()
    if kind == "biome":
        return np.ascontiguousarray(biome.raster(constants, planet)[::stride, ::stride]).tobytes()
    if kind == "water":
        return np.ascontiguousarray(field.water[::stride, ::stride]).astype(np.uint8).tobytes()
    if kind == "rock":
        return np.ascontiguousarray(field.hardness[::stride, ::stride]).astype(np.uint8).tobytes()
    if kind == "province":
        return np.ascontiguousarray(field.province[::stride, ::stride]).astype(np.uint8).tobytes()
    if kind == "river":
        #: How far the nearest fresh water lies, a metre a step. A byte
        #: reaches further than the widest river is (`flow_bank`), and past
        #: that the picture has no use for the number.
        byte = np.iinfo(np.uint8)
        metres = field.river_m[::stride, ::stride]
        return np.ascontiguousarray(np.clip(metres, byte.min, byte.max)).astype(np.uint8).tobytes()
    if kind == "lake":
        #: A lake as a quantity rather than as a class, so the picture can
        #: cut its shore between the cells as it cuts the sea's by the
        #: height. Read as a class it was a lake of whole five-hundred-metre
        #: cells -- blue rectangles with right angles, which is not a lake.
        byte = np.iinfo(np.uint8)
        wet = (field.water[::stride, ::stride] == fields.LAKE) * byte.max
        return np.ascontiguousarray(wet).astype(np.uint8).tobytes()
    if kind == "flow":
        #: The catchment on a log scale, because a river's catchment runs
        #: from twenty square kilometres to four thousand and the eye reads
        #: the small ones as often as the great.
        byte = np.iinfo(np.uint8)
        top = float(field.river_flow_km2.max())
        share = np.log1p(field.river_flow_km2) / math.log1p(top) if top > 0 else field.height * 0
        scaled = np.clip(share * byte.max, byte.min, byte.max)
        return np.ascontiguousarray(scaled[::stride, ::stride]).astype(np.uint8).tobytes()
    return np.ascontiguousarray(field.form[::stride, ::stride]).astype(np.uint8).tobytes()


#: Rasters by field and kind; the fields live for the process (`field.of`),
#: so their ids are stable keys.
_BYTES: dict[tuple[int, str], bytes] = {}
