# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The height raster coded to squeeze (2026-09-18): what the ground rises by
from its neighbours, not how high it stands.

Heights are sixteen-bit decimetres, and gzip made 1.19 MB of Terra's 1.57:
a height is a number of many digits and its neighbour another. What a cell
is **not** told by its neighbours is small: the plane through the cell to
the west, the one to the north and the one between (`west + north -
northwest`) meets the ground within a few decimetres almost everywhere. So
each cell is sent as that remainder -- the grid's second difference, rows
then columns -- folded so small negatives are small numbers too (zigzag),
and with the low bytes of the whole raster first and the high bytes after,
so the near-empty high half is one long run. Squeezed at the same level,
Terra's heights go from 1.19 MB to 0.55, Aurora's from 1.20 to 0.48 and
Pyroxis's from 0.30 to 0.16 (measured 2026-09-18); the decoding is two
running sums, one along the rows and one down the columns.

Every step is taken modulo two to the sixteenth, so the coding is exact for
any heights at all, wrap included; the client undoes it
(`frontend/src/panels/map/planar.ts`) and the two are pinned to one fixture.
Asked for by name (`raster/height.planar`): a page loaded before the coding
existed keeps asking for the plain bytes, and gets them. The coded bytes
are part of the picture's version (`cached.version`), so a change to this
coding is a new address for every browser, never old bytes read by a new
rule -- and a change that keeps the name must still keep the bytes, or the
fixture shared with the client stops agreeing.
"""

from __future__ import annotations

import numpy as np

#: The name the client asks for this coding by: `height.planar`.
PLANAR = "planar"
#: The sign bit of a sixteen-bit height: what the zigzag folds on.
SIGN_SHIFT = 15


def encode(raw: bytes, cols: int) -> bytes:
    """Little-endian sixteen-bit heights, `cols` a row, as the coding sends
    them: the second difference, zigzagged, low bytes then high bytes."""
    heights = np.frombuffer(raw, dtype="<i2").reshape(-1, cols).astype(np.int32)
    rest = np.diff(np.diff(heights, axis=0, prepend=0), axis=1, prepend=0)
    #: Folded into sixteen signed bits by the modulus, as the running sums
    #: that undo it will fold theirs.
    rest = ((rest + (1 << SIGN_SHIFT)) & 0xFFFF) - (1 << SIGN_SHIFT)
    zigzag = ((rest << 1) ^ (rest >> SIGN_SHIFT)).astype("<u2")
    pairs = zigzag.view(np.uint8).reshape(-1, 2)
    return np.concatenate([pairs[:, 0], pairs[:, 1]]).tobytes()


def decode(coded: bytes, cols: int) -> bytes:
    """The plain heights back: what the client does, here for the tests."""
    half = len(coded) // 2
    #: Sixty-four bits for the running sums: a million remainders summed
    #: before the modulus folds them run far past thirty-two.
    low = np.frombuffer(coded[:half], dtype=np.uint8).astype(np.int64)
    high = np.frombuffer(coded[half:], dtype=np.uint8).astype(np.int64)
    zigzag = low | (high << 8)
    rest = (zigzag >> 1) ^ -(zigzag & 1)
    grid = rest.reshape(-1, cols)
    heights = np.cumsum(np.cumsum(grid, axis=1), axis=0)
    return (((heights + (1 << SIGN_SHIFT)) & 0xFFFF) - (1 << SIGN_SHIFT)).astype("<i2").tobytes()
