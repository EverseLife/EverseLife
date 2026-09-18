# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The height raster's planar coding (2026-09-18): exact, smaller, and the
same bytes the client decodes (`frontend/src/panels/map/planar.ts`, pinned
to the same fixture in `planar.test.ts`). The route that sends it is
tested beside the others in `test_api.py`: one app per process."""

from __future__ import annotations

import gzip

import numpy as np

from src import healpix
from src.api import planar
from src.constants import Constants
from src.engine import rasters, terrain
from src.models.world import Planet

#: Three rows of four, the extremes of sixteen bits side by side so the
#: remainders wrap: the client's test decodes these very bytes.
FIXTURE_HEIGHTS = [0, 5, -3, 32767, -32768, 100, 101, 99, 7, 7, -7, 0]
FIXTURE_CODED = [
    0, 10, 15, 251, 255, 65, 18, 248, 241, 56, 29, 18,
    0, 0, 0, 255, 255, 255, 0, 255, 255, 255, 0, 0,
]  # fmt: skip


def test_the_fixture_is_the_one_both_sides_read() -> None:
    raw = np.array(FIXTURE_HEIGHTS, dtype="<i2").tobytes()
    assert list(planar.encode(raw, 4)) == FIXTURE_CODED
    assert planar.decode(bytes(FIXTURE_CODED), 4) == raw


def test_the_coding_is_exact_and_squeezes_to_about_half(constants: Constants) -> None:
    for planet in (Planet.TERRA, Planet.PYROXIS):
        field = terrain.field_of(constants, planet)
        for nside in terrain.picture_nsides(field):
            raw = rasters.raster_bytes(constants, planet, "height", nside)
            assert raw is not None
            _rows, cols = healpix.tile_shape(nside)
            coded = planar.encode(raw, cols)
            assert len(coded) == len(raw)
            assert planar.decode(coded, cols) == raw, (planet, nside)
    #: The point of it: Terra's whole picture, squeezed as it is sent --
    #: 0.46 of the plain bytes when measured (2026-09-18); held to a bound
    #: with room over it, since a rebuilt vault moves the ground under it.
    field = terrain.field_of(constants, Planet.TERRA)
    raw = rasters.raster_bytes(constants, Planet.TERRA, "height")
    assert raw is not None
    _rows, cols = healpix.tile_shape(terrain.raster_nside(field))
    plain, coded = len(gzip.compress(raw, 6)), len(gzip.compress(planar.encode(raw, cols), 6))
    assert coded < 0.6 * plain, (plain, coded)


def test_the_version_names_the_coded_heights_too(constants: Constants, monkeypatch) -> None:
    """A coding whose bytes change is a new version, so a browser never keeps
    a year's worth of heights coded by one rule under the name it will read
    them by another (review, 2026-09-18)."""
    from src.api import cached

    before = cached.version(constants, Planet.TERRA)
    monkeypatch.setattr(cached, "_VERSIONS", {})
    monkeypatch.setattr(cached, "_PLANAR", {})
    monkeypatch.setattr(planar, "encode", lambda raw, cols: bytes(reversed(raw)))
    assert cached.version(constants, Planet.TERRA) != before
