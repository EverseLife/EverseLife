# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Noise on the sphere and the tiling of a planet -- what the field's reader still needs.

A library, not a rule of the game -- like `astro` and `globe`. Until the
landscape plan's wave 2 this module built a planet's relief out of noise;
the planet is now the vault's field (`src.field`), and what remains here is
what that field is read with:

* **value noise on the unit sphere** (`noise_at`): a point's reading off a
  lattice of random values in three dimensions, summed over a few halving
  octaves. Sampled through x, y, z rather than latitude and longitude, so
  there is no seam at the antimeridian and no pole. The ground's marks --
  woods, stones, meadow -- still read it, seeded off the field's seed, until
  the facets of the plan say where the woods are;
* **the tiling** (`TILE_DEG`, `TILE_N`): the squares the client asks the
  close ground by (D-323), a tenth of a degree a step;
* **the rays round a place** (`around`): what lies within a walk of it.

The numbers in it are the mathematics' own -- the octaves, the lattice --
and that is why the magic-number test does not look here.
"""

from __future__ import annotations

import math

import numpy as np

#: The noise: how many halving octaves are summed, how the amplitude falls
#: between them, and how many lattice cells the first octave lays across the
#: sphere's diameter.
OCTAVES = 5
PERSISTENCE = 0.5
LATTICE = 3.0
#: The mix of large and small: a planet is a few continents and many bays.
ROUGHNESS = 2.0
#: How many tiles the server keeps written at once: a walker's
#: neighbourhood and a frame's worth, not a planet's.
TILE_KEEP = 64
#: How finely a tile's numbers are written on the wire: a hundred-thousandth
#: is a thousandth of a percent of the rise, well under any line drawn.
TILE_DECIMALS = 5
#: The tiles the ground is read from: degrees a side, steps a side. A tenth
#: of a degree a step -- forty-odd metres on a world this small -- and
#: ten thousand numbers a tile.
TILE_DEG = 10.0
TILE_N = 100


def _hash3(seed: int, x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    """A lattice corner's value in [0, 1): one integer hash, the same on every machine."""
    h = (
        x.astype(np.int64) * 374761393
        + y.astype(np.int64) * 668265263
        + z.astype(np.int64) * 2147483647
        + int(seed) * 1274126177
    ) & 0xFFFFFFFF
    h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
    h = (h ^ (h >> 16)) & 0xFFFFFFFF
    return h / float(0x100000000)


def _smooth(t: np.ndarray) -> np.ndarray:
    return t * t * (3.0 - 2.0 * t)


def _value_noise(seed: int, px: np.ndarray, py: np.ndarray, pz: np.ndarray) -> np.ndarray:
    """Trilinear value noise of points in lattice units, in [0, 1]."""
    x0, y0, z0 = np.floor(px), np.floor(py), np.floor(pz)
    fx, fy, fz = _smooth(px - x0), _smooth(py - y0), _smooth(pz - z0)
    out = np.zeros_like(px)
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                corner = _hash3(seed, x0 + dx, y0 + dy, z0 + dz)
                weight = (fx if dx else 1 - fx) * (fy if dy else 1 - fy) * (fz if dz else 1 - fz)
                out = out + corner * weight
    return out


def heights(seed: int, lat: np.ndarray, lon: np.ndarray, lattice: float = LATTICE) -> np.ndarray:
    """The noise in [0, 1] at these points of the sphere, degrees in, fractal
    noise out. The lattice is how many cells of the first octave go round the
    planet: the land's own by default, finer for the facets' mosaic (wave 7)."""
    return _fractal(seed, lat, lon, lattice, OCTAVES)


def _fractal(
    seed: int, lat: np.ndarray, lon: np.ndarray, lattice: float, octaves: int
) -> np.ndarray:
    phi, lam = np.radians(lat), np.radians(lon)
    x = np.cos(phi) * np.cos(lam)
    y = np.cos(phi) * np.sin(lam)
    z = np.sin(phi)
    total = np.zeros_like(x, dtype=float)
    amplitude = 1.0
    frequency = lattice
    norm = 0.0
    for octave in range(octaves):
        #: Each octave is offset along the lattice so the octaves do not share
        #: their corners: summed in place they would ring at the same points.
        shift = octave * 17.0
        total += amplitude * _value_noise(
            seed + octave, x * frequency + shift, y * frequency + shift, z * frequency + shift
        )
        norm += amplitude
        amplitude *= PERSISTENCE
        frequency *= ROUGHNESS
    return total / norm


def grain_at(seed: int, lat: float, lon: float, cells: float) -> float:
    """One flat draw per patch of the sphere, in [0, 1): the point's cell of a
    lattice of `cells` cells to the radius, hashed.

    Flat on purpose (landscape plan wave 7): the fractal noise above is a sum
    of octaves and comes out bell-shaped -- half its readings sit between 0.4
    and 0.6 -- so dividing anything by its value gives the middle of a table
    the lion's share. A hash of the cell is uniform, and a facet is a patch of
    ground rather than a gradient, so the patch is what it should be drawn on.
    """
    phi, lam = math.radians(lat), math.radians(lon)
    x = math.cos(phi) * math.cos(lam) * cells
    y = math.cos(phi) * math.sin(lam) * cells
    z = math.sin(phi) * cells
    return _hash3(
        seed, np.array([math.floor(x)]), np.array([math.floor(y)]), np.array([math.floor(z)])
    )[0]


def noise_at(seed: int, lat: float, lon: float, lattice: float = LATTICE) -> float:
    """One reading of the fractal noise at a point, in [0, 1]."""
    return float(
        heights(seed, np.array([lat], dtype=float), np.array([lon], dtype=float), lattice)[0]
    )


def around(lat: float, lon: float, reach_deg: float) -> list[tuple[float, float]]:
    """Points within `reach_deg` of a place, on `AROUND_RAYS` bearings at
    `AROUND_STEPS` distances: what lies within a walk of it is read here."""
    out: list[tuple[float, float]] = []
    stretch = max(1e-6, math.cos(math.radians(lat)))
    for ray in range(AROUND_RAYS):
        bearing = 2 * math.pi * ray / AROUND_RAYS
        for step in range(1, AROUND_STEPS + 1):
            d = reach_deg * step / AROUND_STEPS
            out.append((lat + d * math.cos(bearing), lon + d * math.sin(bearing) / stretch))
    return out


AROUND_RAYS = 8
AROUND_STEPS = 3


def tile_counts() -> tuple[int, int]:
    """How many tiles cover the sphere: rows of latitude, columns of longitude."""
    return int(round(180.0 / TILE_DEG)), int(round(360.0 / TILE_DEG))


def tile_of(lat: float, lon: float) -> tuple[int, int]:
    """Which tile a point lies on."""
    rows, cols = tile_counts()
    row = min(rows - 1, max(0, int(math.floor((lat + 90.0) / TILE_DEG))))
    col = int(math.floor((lon + 180.0) / TILE_DEG)) % cols
    return row, col


def tile_origin(row: int, col: int) -> tuple[float, float]:
    """A tile's south-west corner, degrees."""
    return -90.0 + row * TILE_DEG, -180.0 + col * TILE_DEG
