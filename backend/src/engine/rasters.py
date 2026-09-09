# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The rasters a planet's picture is drawn from (landscape plan wave 5, §9.3).

Three arrays the shader reads as textures: the height in signed metres,
sixteen bits a cell; the biome as a byte into `biome.names`; the landform
as a byte into the field's table. Thinned to `terrain.RASTER_ROWS_MAX`
rows, written once per field and kept: a constant of the vault must not be
encoded on every request. The shader draws by them and judges nothing --
land, aim and find stay the server's, off the field itself (plan §9.1).
"""

from __future__ import annotations

import numpy as np

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
        metres = np.round(field.height[::stride, ::stride].astype(np.float64) * field.relief_m)
        short = np.iinfo(np.int16)
        return np.clip(metres, short.min, short.max).astype("<i2").tobytes()
    if kind == "biome":
        return np.ascontiguousarray(biome.raster(constants, planet)[::stride, ::stride]).tobytes()
    return np.ascontiguousarray(field.form[::stride, ::stride]).astype(np.uint8).tobytes()


#: Rasters by field and kind; the fields live for the process (`field.of`),
#: so their ids are stable keys.
_BYTES: dict[tuple[int, str], bytes] = {}
