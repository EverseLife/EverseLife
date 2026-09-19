# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Where a city ends (D-356): its outline, read as a law over the land.

Outside `engine` on purpose, beside `globe` and `weather`: this is arithmetic
over a field, and the rule modules hold no numbers (D-065). Every number that
moves the line -- the field's power, the least reach, the raster -- is the
vault's (`city.outline_*`) and is read off the registry into an `OutlineLaw`;
what stays here is the formula's own frame: the level the edge lies at, how
far past a disc the raster reaches, the floors that keep it finite.

The map has drawn a city this way since the D-323 addendum
(`frontend/src/panels/map/territory.ts`): each node of the city's frame a
disc of its own land in a metaball field, `(r / d)^p`; the shortest tree that
joins them (Prim) and the ways between them (D-332) as capsules of the same
field; the field rastered, and where it is one lies the edge; a hole dropped,
since a city has none. Since D-356 the outline is also whose land a node is,
so the engine reads the very same field: a point lies within the city when
the raster's field there is one or more, or when the edge encloses it in a
hole.

What the engine does not do is draw. The map traces the level line
(marching squares) and rounds it (Chaikin); the engine asks the raster by
bilinear interpolation. The two agree on every edge of a cell and part inside
one by the rounding, so a node whose centre stands on the line stands on it
in the picture as well. A saddle cell -- two corners in, two out, across --
is read by its middle on both sides: the middle goes with the corners on its
own side of the level, so a pocket of the outside that reaches the open only
through such a corner is open or a hole alike for the map and the engine.

One thing the picture drops is not dropped here: a speck of land smaller
than a cell. The field has no maximum away from the discs and the isthmuses
-- every term falls with the distance to a point or a segment -- so a speck
is the raster sampling a neck of the city's own land, not ground of its own,
and it lies on the line's land either way.

Both sides lay the frame out in key order: the tree is a property of the
points, but among equal gaps which pair joins first is a property of the
order, and the order of map rows is nobody's law. And both sum in that order,
one term after another -- Python's own `sum` compensates, and the map's
`reduce` does not.

This copy and the map's are pinned by one fixture (`tests/test_outline.py`
here, `territory.test.ts` on the client): the two trees cannot import each
other, and they meet in those numbers.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from src.constants import Constants
from src.constants import registry as R

RAD = math.pi / 180
#: Where the field is this, the edge lies: the field of one disc on its own
#: rim, whatever the power.
LEVEL = 1.0
#: How many radii of a disc the raster reaches past its centre: at the fourth
#: power the field of one disc there is a sixteenth, and the blot does not
#: reach it. The map's `traceField` reaches as far.
REACH_RADII = 2
#: The margin an isthmus swells to, in its own half-widths.
SWELL_RADII = 2
#: A disc's land is never under a square metre: a node of no area is a point.
LEAST_AREA_M2 = 1.0
#: The typical spacing is never under a metre: a city whose nodes stand on
#: one spot would otherwise have no scale to raster at.
LEAST_SPACING_M = 1.0
#: Below this cosine a degree of longitude is taken as at the pole.
COS_FLOOR = 1e-6
#: The field at a node's own centre is infinite; the raster is clipped here
#: before it is interpolated, so a cell beside a centre is land and not NaN.
CLIP = 1e12


@dataclass(frozen=True, slots=True)
class OutlineLaw:
    """The outline's numbers, the vault's (`city.outline_*`)."""

    #: How fast a disc's field falls off with the distance, `(r / d)^power`:
    #: the fourth power, not the square, so the edge lies close to the discs'
    #: own -- the land of the nodes, not a swell round it -- and two
    #: neighbours still join (D-323 addendum).
    power: float
    #: The least a node's land reaches, as a share of the typical spacing.
    reach_share: float
    #: The least a lone node's land reaches, metres.
    lone_reach_m: float
    #: Raster cells across the city's typical spacing.
    cells_per_step: float
    #: The cap on raster cells a side.
    max_cells: float
    #: How wide an isthmus is, in raster cells each side of its axis.
    bridge_cells: float

    @property
    def margin_cells(self) -> float:
        """What the swell of the isthmuses takes off the raster, in cells:
        the bridge's own width at both edges of the picture."""
        return 2 * SWELL_RADII * self.bridge_cells


def law_of(constants: Constants) -> OutlineLaw:
    return OutlineLaw(
        power=float(constants[R.CITY_OUTLINE_POWER]),
        reach_share=float(constants[R.CITY_OUTLINE_REACH_SHARE]),
        lone_reach_m=float(constants[R.CITY_OUTLINE_LONE_REACH_M]),
        cells_per_step=float(constants[R.CITY_OUTLINE_CELLS_PER_STEP]),
        max_cells=float(constants[R.CITY_OUTLINE_MAX_CELLS]),
        bridge_cells=float(constants[R.CITY_OUTLINE_BRIDGE_CELLS]),
    )


@dataclass(frozen=True, slots=True)
class Member:
    """A node of the frame as the field sees it: where it stands and its land."""

    key: str
    lat: float
    lon: float
    area_m2: float


@dataclass(frozen=True, slots=True)
class Outline:
    """One city's land: the field rastered over its own flat plane.

    The plane is metres about the frame's middle, as the map lays it out;
    `values[j, i]` is the field at `(x0 + i * cell, y0 + j * cell)`, and
    `open` marks the samples below the level that the outside reaches.
    """

    lat0: float
    lon0: float
    stretch: float
    per_deg: float
    x0: float
    y0: float
    cell: float
    values: np.ndarray
    open: np.ndarray

    def covers(self, lat: float, lon: float) -> bool:
        """Whether the point lies within the city's land."""
        x = (lon - self.lon0) * self.stretch * self.per_deg
        y = (lat - self.lat0) * self.per_deg
        ny, nx = self.values.shape
        fi = (x - self.x0) / self.cell
        fj = (y - self.y0) / self.cell
        if not (0 <= fi <= nx - 1 and 0 <= fj <= ny - 1):
            return False
        i = min(int(fi), nx - 2)
        j = min(int(fj), ny - 2)
        t = fi - i
        u = fj - j
        v = self.values
        field = (
            (1 - t) * (1 - u) * v[j, i]
            + t * (1 - u) * v[j, i + 1]
            + (1 - t) * u * v[j + 1, i]
            + t * u * v[j + 1, i + 1]
        )
        if field >= LEVEL:
            return True
        #: Below the level: land only if it is a hole. Which part of the
        #: outside the point is in is told by the nearest corner outside the
        #: edge: in a saddle the two outside corners can be two parts, one
        #: open and one a hole, and the point belongs to its own.
        corners = [
            (corner, (t - di) ** 2 + (u - dj) ** 2)
            for corner, di, dj in (
                ((j, i), 0, 0),
                ((j, i + 1), 1, 0),
                ((j + 1, i), 0, 1),
                ((j + 1, i + 1), 1, 1),
            )
            if v[corner] < LEVEL
        ]
        nearest = min(corners, key=lambda one: one[1])[0]
        return not self.open[nearest]

    def window(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """The raster's reach in degrees: (south, north), (west, east).
        Nothing outside it can be land."""
        ny, nx = self.values.shape
        west = self.lon0 + self.x0 / (self.stretch * self.per_deg)
        east = self.lon0 + (self.x0 + (nx - 1) * self.cell) / (self.stretch * self.per_deg)
        south = self.lat0 + self.y0 / self.per_deg
        north = self.lat0 + (self.y0 + (ny - 1) * self.cell) / self.per_deg
        return (south, north), (west, east)


def _mean(values: Sequence[float]) -> float:
    #: One term after another, as the map's `reduce` sums (the module's note).
    total = 0.0
    for value in values:
        total += value
    return total / len(values)


def _typical_spacing(xs: Sequence[float], ys: Sequence[float], law: OutlineLaw) -> float:
    """The median distance from a node to its nearest neighbour."""
    if len(xs) < 2:
        return law.lone_reach_m
    nearest = []
    for i in range(len(xs)):
        best = math.inf
        for j in range(len(xs)):
            if i != j:
                best = min(best, math.hypot(xs[i] - xs[j], ys[i] - ys[j]))
        nearest.append(best)
    nearest.sort()
    return max(LEAST_SPACING_M, nearest[len(nearest) // 2])


def _span(xs: Sequence[float], ys: Sequence[float]) -> list[tuple[int, int]]:
    """The shortest tree that joins every node (Prim), as pairs of discs.

    Every node ends up on it, so the blot is one however the city is spread;
    and it is the shortest such tree, so nothing is bridged that a nearer
    pair has already joined. The map's `spanOf`, step for step.
    """
    n = len(xs)
    if n < 2:
        return []
    on_tree = [False] * n
    run = [math.inf] * n
    source = [0] * n
    pairs: list[tuple[int, int]] = []
    on_tree[0] = True
    for i in range(1, n):
        run[i] = (xs[0] - xs[i]) ** 2 + (ys[0] - ys[i]) ** 2
    for _ in range(1, n):
        chosen = -1
        for i in range(n):
            if not on_tree[i] and (chosen < 0 or run[i] < run[chosen]):
                chosen = i
        pairs.append((source[chosen], chosen))
        on_tree[chosen] = True
        for i in range(n):
            if on_tree[i]:
                continue
            far = (xs[chosen] - xs[i]) ** 2 + (ys[chosen] - ys[i]) ** 2
            if far < run[i]:
                run[i] = far
                source[i] = chosen
    return pairs


def _capsules(*laid: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """The isthmuses of the tree and the streets, each pair once: a street
    that is also a limb of the tree would otherwise count twice in the field
    and swell to a wider neck than its neighbours."""
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int]] = []
    for pairs in laid:
        for i, j in pairs:
            pair = (min(i, j), max(i, j))
            if pair in seen:
                continue
            seen.add(pair)
            out.append((i, j))
    return out


def _reached(values: np.ndarray) -> np.ndarray:
    """The samples below the level that the outside reaches.

    A flood from the raster's border over the samples outside the edge, from
    one to its four neighbours -- and across a saddle cell where the trace
    joins the two outside corners, that is where the cell's middle is below
    the level too (`marchingSquares` reads the saddle by the same mean).
    """
    below = values < LEVEL
    mean = (values[:-1, :-1] + values[:-1, 1:] + values[1:, :-1] + values[1:, 1:]) / 4
    #: A saddle whose two outside corners are joined through the middle: one
    #: array per diagonal, indexed by the cell's lower-left sample.
    rising = below[:-1, :-1] & below[1:, 1:] & ~below[:-1, 1:] & ~below[1:, :-1] & (mean < LEVEL)
    falling = below[:-1, 1:] & below[1:, :-1] & ~below[:-1, :-1] & ~below[1:, 1:] & (mean < LEVEL)
    reached = np.zeros_like(below)
    reached[0, :] = below[0, :]
    reached[-1, :] = below[-1, :]
    reached[:, 0] |= below[:, 0]
    reached[:, -1] |= below[:, -1]
    while True:
        grown = reached.copy()
        grown[1:, :] |= reached[:-1, :]
        grown[:-1, :] |= reached[1:, :]
        grown[:, 1:] |= reached[:, :-1]
        grown[:, :-1] |= reached[:, 1:]
        grown[1:, 1:] |= reached[:-1, :-1] & rising
        grown[:-1, :-1] |= reached[1:, 1:] & rising
        grown[1:, :-1] |= reached[:-1, 1:] & falling
        grown[:-1, 1:] |= reached[1:, :-1] & falling
        grown &= below
        if np.array_equal(grown, reached):
            return reached
        reached = grown


def outline_of(
    members: Iterable[Member],
    ways: Iterable[tuple[str, str]],
    radius_m: float,
    law: OutlineLaw,
) -> Outline | None:
    """A city's outline from its frame and its streets (the map's `outlineOf`).

    `ways` are pairs of member keys; one to anybody else is not the city's
    street and is passed over. None for a frame with no member at all.

    Remembered by what it is made of: the line is asked at every tick and
    every scout's return, and a frame changes a few times a day. The key is
    the whole input -- the members, the streets, the radius and the numbers
    -- so a changed frame is a new line, and nothing is ever stale.
    """
    placed = tuple(sorted({member.key: member for member in members}.values(), key=lambda m: m.key))
    if not placed:
        return None
    keys = {member.key for member in placed}
    streets = tuple(
        sorted({(min(a, b), max(a, b)) for a, b in ways if a in keys and b in keys and a != b})
    )
    return _outline(placed, streets, radius_m, law)


#: How many lines are kept: a few per city, the frame changing now and then.
LINES_KEPT = 64


@functools.lru_cache(maxsize=LINES_KEPT)
def _outline(
    placed: tuple[Member, ...],
    ways: tuple[tuple[str, str], ...],
    radius_m: float,
    law: OutlineLaw,
) -> Outline:
    lat0 = _mean([member.lat for member in placed])
    lon0 = _mean([member.lon for member in placed])
    stretch = max(COS_FLOOR, math.cos(lat0 * RAD))
    per_deg = radius_m * RAD
    xs = [(member.lon - lon0) * stretch * per_deg for member in placed]
    ys = [(member.lat - lat0) * per_deg for member in placed]
    spacing = _typical_spacing(xs, ys, law)
    least = spacing * law.reach_share if len(placed) > 1 else law.lone_reach_m
    radii = [
        max(least, math.sqrt(max(LEAST_AREA_M2, member.area_m2) / math.pi)) for member in placed
    ]
    index = {member.key: i for i, member in enumerate(placed)}
    streets = [(index[a], index[b]) for a, b in ways]

    #: The raster: it covers the blot's farthest reach and a cell more, at a
    #: cell that keeps it within `max_cells` a side and leaves room for the
    #: isthmuses' swell (the map's `traceField`, number for number).
    reach = max(r * REACH_RADII for r in radii)
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    cell = max(
        spacing / law.cells_per_step,
        (span_x + 2 * reach) / law.max_cells,
        (span_y + 2 * reach) / law.max_cells,
        span_x / (law.max_cells - law.margin_cells),
        span_y / (law.max_cells - law.margin_cells),
    )
    bridges = _capsules(_span(xs, ys), streets)
    bridge_r = max(least, cell * law.bridge_cells) if bridges else 0.0
    edge = max(reach, bridge_r * SWELL_RADII)
    x0 = min(xs) - edge
    x1 = x0 + span_x + 2 * edge
    y0 = min(ys) - edge
    y1 = y0 + span_y + 2 * edge
    nx = math.ceil((x1 - x0) / cell) + 1
    ny = math.ceil((y1 - y0) / cell) + 1

    gx = x0 + np.arange(nx, dtype=np.float64) * cell
    gy = y0 + np.arange(ny, dtype=np.float64) * cell
    px, py = np.meshgrid(gx, gy)
    field = np.zeros((ny, nx), dtype=np.float64)
    #: `(r / d)^p` as `(r^2 / d^2)^(p/2)`: the square of the distance is what
    #: is measured, and the map writes the term the same way.
    half = law.power / 2
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        for x, y, r in zip(xs, ys, radii, strict=True):
            dd = (px - x) ** 2 + (py - y) ** 2
            field += np.where(dd > 0, ((r * r) / dd) ** half, np.inf)
        wide = bridge_r * bridge_r
        for i, j in bridges:
            ax, ay, bx, by = xs[i], ys[i], xs[j], ys[j]
            vx = bx - ax
            vy = by - ay
            run = vx * vx + vy * vy
            if run > 0:
                along = np.clip(((px - ax) * vx + (py - ay) * vy) / run, 0, 1)
            else:
                along = np.zeros_like(px)
            dd = (px - ax - along * vx) ** 2 + (py - ay - along * vy) ** 2
            field += np.where(dd > 0, (wide / dd) ** half, np.inf)
    values = np.minimum(field, CLIP)
    return Outline(
        lat0=lat0,
        lon0=lon0,
        stretch=stretch,
        per_deg=per_deg,
        x0=x0,
        y0=y0,
        cell=cell,
        values=values,
        open=_reached(values),
    )
