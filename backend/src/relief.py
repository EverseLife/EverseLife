# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The relief of a sphere: a height field, its seas, its mountains and its rivers (D-319).

A library, not a rule of the game -- like `astro` and `globe`. Nothing here
knows a planet, a node or a vault constant: it takes a seed and a few shares,
and answers with heights, water and lines. The numbers in it are the
mathematics' own -- the octaves of the noise, the size of the lattice, the
grid the field is sampled on -- and that is why the magic-number test does
not look here; what the world is made of (how much of it is sea, how many
rivers run) lives in the vault and is passed in.

## How the field is made

Value noise on the unit sphere: a point's height is read off a lattice of
random heights in three dimensions, interpolated smoothly, and summed over a
few octaves of halving size. Sampling the sphere through its three
coordinates rather than through latitude and longitude is what keeps the
poles and the antimeridian seamless: there is no edge to the field anywhere.

The field is sampled once on a grid of latitude by longitude -- fine enough
that a city and its surroundings read the same height at every one of their
nodes, coarse enough that a whole planet is a few thousand numbers. The sea
level is the height that puts the asked share of the grid under water; the
mountain line, the height that leaves the asked share of the land above it.

## Rivers

A river starts at one of the highest cells of the land and walks downhill,
one cell to the steepest neighbour at a time, until it reaches the sea or a
cell with nowhere lower to go -- a basin, which becomes a lake. The walk is a
line of cell centres, and that line is what the map draws and what a node
measures its distance to. No basin filling, no meanders: what the surface
needs is that a river lies where the land slopes and ends where the water
is, and this gives exactly that, deterministically, for a few dozen rivers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

#: The grid the field is sampled on: rows of latitude, columns of longitude.
#: Two degrees a cell -- 220 km on Terra -- fine enough for a planet's shape,
#: and eight thousand cells in all.
GRID_ROWS = 90
GRID_COLS = 180
#: The noise: how many halving octaves are summed, how the amplitude falls
#: between them, and how many lattice cells the first octave lays across the
#: sphere's diameter.
OCTAVES = 5
PERSISTENCE = 0.5
LATTICE = 3.0
#: The mix of large and small: a planet is a few continents and many bays.
ROUGHNESS = 2.0
#: How far apart two rivers' sources keep, in degrees of arc. By arc, not by
#: cells: a polar row is a hundred and eighty cells round one point, and a
#: cap of high ground there would seat a river on every third of them -- a
#: fan of rivers out of the pole, all within a step of each other.
SOURCE_SPACING_DEG = 5.0


def _hash3(seed: int, x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    """A lattice corner's height in [0, 1): one integer hash, the same on every machine."""
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


def heights(seed: int, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """The height in [0, 1] at these points of the sphere, degrees in, fractal noise out."""
    phi, lam = np.radians(lat), np.radians(lon)
    x = np.cos(phi) * np.cos(lam)
    y = np.cos(phi) * np.sin(lam)
    z = np.sin(phi)
    total = np.zeros_like(x, dtype=float)
    amplitude = 1.0
    frequency = LATTICE
    norm = 0.0
    for octave in range(OCTAVES):
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


def noise_at(seed: int, lat: float, lon: float) -> float:
    """One reading of the fractal noise at a point, in [0, 1]."""
    return float(heights(seed, np.array([lat], dtype=float), np.array([lon], dtype=float))[0])


def row_latitudes() -> list[float]:
    """The latitude at the middle of each row of the grid, south to north."""
    return [-90.0 + (row + 0.5) * (180.0 / GRID_ROWS) for row in range(GRID_ROWS)]


def _cell_centres() -> tuple[np.ndarray, np.ndarray]:
    lat = -90.0 + (np.arange(GRID_ROWS) + 0.5) * (180.0 / GRID_ROWS)
    lon = -180.0 + (np.arange(GRID_COLS) + 0.5) * (360.0 / GRID_COLS)
    return np.meshgrid(lat, lon, indexing="ij")


@dataclass(frozen=True, slots=True)
class Field:
    """A planet's relief: the grid, its water lines and its rivers."""

    seed: int
    grid: np.ndarray
    sea_level: float
    mountain_level: float
    #: Each river a list of (lat, lon) cell centres, source first.
    rivers: tuple[tuple[tuple[float, float], ...], ...]
    #: The cells that are lakes: rivers ended there with nowhere lower to go.
    lakes: frozenset[tuple[int, int]]

    # --- reading -------------------------------------------------------------

    def cell(self, lat: float, lon: float) -> tuple[int, int]:
        row = int((lat + 90.0) / (180.0 / GRID_ROWS))
        col = int((lon + 180.0) / (360.0 / GRID_COLS))
        return min(GRID_ROWS - 1, max(0, row)), col % GRID_COLS

    def centre(self, row: int, col: int) -> tuple[float, float]:
        return (
            -90.0 + (row + 0.5) * (180.0 / GRID_ROWS),
            -180.0 + (col + 0.5) * (360.0 / GRID_COLS),
        )

    def height(self, lat: float, lon: float) -> float:
        """The height at a point: the field interpolated between its cells."""
        row = (lat + 90.0) / (180.0 / GRID_ROWS) - 0.5
        col = (lon + 180.0) / (360.0 / GRID_COLS) - 0.5
        r0 = int(math.floor(row))
        c0 = int(math.floor(col))
        fr, fc = row - r0, col - c0
        rows = [min(GRID_ROWS - 1, max(0, r0)), min(GRID_ROWS - 1, max(0, r0 + 1))]
        cols = [c0 % GRID_COLS, (c0 + 1) % GRID_COLS]
        top = self.grid[rows[0], cols[0]] * (1 - fc) + self.grid[rows[0], cols[1]] * fc
        bottom = self.grid[rows[1], cols[0]] * (1 - fc) + self.grid[rows[1], cols[1]] * fc
        return float(top * (1 - fr) + bottom * fr)

    def is_sea(self, lat: float, lon: float) -> bool:
        return self.height(lat, lon) < self.sea_level

    def is_lake(self, lat: float, lon: float) -> bool:
        return self.cell(lat, lon) in self.lakes

    def is_water(self, lat: float, lon: float) -> bool:
        return self.is_sea(lat, lon) or self.is_lake(lat, lon)

    def is_mountain(self, lat: float, lon: float) -> bool:
        return self.height(lat, lon) >= self.mountain_level

    def relief(self, lat: float, lon: float) -> float:
        """How far above the sea the point stands, as a share of the range above sea level."""
        span = max(1e-9, 1.0 - self.sea_level)
        return max(0.0, (self.height(lat, lon) - self.sea_level) / span)

    def river_distance_deg(self, lat: float, lon: float) -> float:
        """How far the nearest river runs, in degrees of arc -- infinite where there is none."""
        best = math.inf
        phi = math.radians(lat)
        for river in self.rivers:
            for a, b in zip(river, river[1:], strict=False):
                best = min(best, _segment_distance(phi, lat, lon, a, b))
            if len(river) == 1:
                best = min(best, _point_distance(phi, lat, lon, river[0]))
        return best

    def river_crossed(self, a: tuple[float, float], b: tuple[float, float]) -> bool:
        """Whether the straight way from `a` to `b` crosses a river's line.

        The rivers are polylines through cell centres; a way of metres is a
        hair beside them, so the test is plain segment intersection on the
        local flat map (D-321: a river is crossed at a ford, not by aiming).
        """
        phi = math.radians(a[0])
        for river in self.rivers:
            for p, q in zip(river, river[1:], strict=False):
                if _segments_cross(phi, a, b, p, q):
                    return True
        return False

    def land_share(self) -> float:
        return float(np.mean(self.grid >= self.sea_level))


def _flat(phi: float, origin: tuple[float, float], p: tuple[float, float]) -> tuple[float, float]:
    return (((p[1] - origin[1] + 180.0) % 360.0 - 180.0) * math.cos(phi), p[0] - origin[0])


def _segments_cross(
    phi: float,
    a: tuple[float, float],
    b: tuple[float, float],
    p: tuple[float, float],
    q: tuple[float, float],
) -> bool:
    """Whether segments a-b and p-q cross on the flat map round `a`."""
    ax, ay = 0.0, 0.0
    bx, by = _flat(phi, a, b)
    px, py = _flat(phi, a, p)
    qx, qy = _flat(phi, a, q)

    def side(x1: float, y1: float, x2: float, y2: float, x3: float, y3: float) -> float:
        return (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)

    s1, s2 = side(ax, ay, bx, by, px, py), side(ax, ay, bx, by, qx, qy)
    s3, s4 = side(px, py, qx, qy, ax, ay), side(px, py, qx, qy, bx, by)
    return (s1 > 0) != (s2 > 0) and (s3 > 0) != (s4 > 0) and 0 not in (s1, s2, s3, s4)


def _point_distance(phi: float, lat: float, lon: float, p: tuple[float, float]) -> float:
    dlon = ((p[1] - lon + 180.0) % 360.0) - 180.0
    return math.hypot(p[0] - lat, dlon * math.cos(phi))


def _segment_distance(
    phi: float, lat: float, lon: float, a: tuple[float, float], b: tuple[float, float]
) -> float:
    """Distance from a point to a segment of cell centres, on the local flat map."""
    cos = math.cos(phi)
    ax = ((a[1] - lon + 180.0) % 360.0 - 180.0) * cos
    ay = a[0] - lat
    bx = ((b[1] - lon + 180.0) % 360.0 - 180.0) * cos
    by = b[0] - lat
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length))
    return math.hypot(ax + t * dx, ay + t * dy)


# --- building --------------------------------------------------------------


def _level_for_share(grid: np.ndarray, below: float) -> float:
    """The height that puts this share of the grid under it."""
    if below <= 0.0:
        return float(grid.min()) - 1e-9
    if below >= 1.0:
        return float(grid.max()) + 1e-9
    return float(np.quantile(grid, below))


def _trace_river(
    grid: np.ndarray, sea_level: float, start: tuple[int, int]
) -> tuple[list[tuple[int, int]], bool]:
    """Walk downhill from the start; returns the cells and whether the sea was reached."""
    path = [start]
    seen = {start}
    row, col = start
    while True:
        here = grid[row, col]
        best: tuple[int, int] | None = None
        lowest = here
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                r, c = row + dr, (col + dc) % GRID_COLS
                if r < 0 or r >= GRID_ROWS or (r, c) in seen:
                    continue
                if grid[r, c] < lowest:
                    lowest = grid[r, c]
                    best = (r, c)
        if best is None:
            return path, False
        path.append(best)
        seen.add(best)
        row, col = best
        if grid[row, col] < sea_level:
            return path, True


def build(seed: int, *, sea_share: float, mountain_share: float, rivers: int) -> Field:
    """The relief of a sphere for this seed and these shares.

    Deterministic: the same seed and shares give the same field on every
    machine, so two servers replaying one world lay the same continents.
    """
    lat, lon = _cell_centres()
    grid = heights(seed, lat, lon)
    sea_level = _level_for_share(grid, sea_share)
    land = grid[grid >= sea_level]
    mountain_level = (
        _level_for_share(land, 1.0 - mountain_share) if land.size else float(grid.max()) + 1e-9
    )
    lines: list[tuple[tuple[float, float], ...]] = []
    lakes: set[tuple[int, int]] = set()
    if rivers > 0 and land.size:
        #: Sources: the highest land cells, one river each, taken in height
        #: order and never twice from one source. Spread apart by arc so a
        #: range does not send every river down the same valley.
        order = np.argsort(grid, axis=None)[::-1]
        taken: list[tuple[float, float]] = []
        for flat in order:
            if len(lines) >= rivers:
                break
            row, col = divmod(int(flat), GRID_COLS)
            if grid[row, col] < sea_level:
                continue
            here = _centre(row, col)
            if any(_arc_deg(here, there) < SOURCE_SPACING_DEG for there in taken):
                continue
            path, reached_sea = _trace_river(grid, sea_level, (row, col))
            if len(path) < 2:
                continue
            taken.append(here)
            if not reached_sea:
                lakes.add(path[-1])
            lines.append(tuple(_centre(r, c) for r, c in path))
    return Field(
        seed=seed,
        grid=grid,
        sea_level=sea_level,
        mountain_level=mountain_level,
        rivers=tuple(lines),
        lakes=frozenset(lakes),
    )


def _arc_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    """The angle between two (lat, lon) points of the sphere, in degrees."""
    phi_a, lam_a = math.radians(a[0]), math.radians(a[1])
    phi_b, lam_b = math.radians(b[0]), math.radians(b[1])
    cos = math.sin(phi_a) * math.sin(phi_b) + math.cos(phi_a) * math.cos(phi_b) * math.cos(
        lam_a - lam_b
    )
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def _centre(row: int, col: int) -> tuple[float, float]:
    return (
        -90.0 + (row + 0.5) * (180.0 / GRID_ROWS),
        -180.0 + (col + 0.5) * (360.0 / GRID_COLS),
    )
