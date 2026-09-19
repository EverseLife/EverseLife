# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Where a city ends, as the engine reads it (D-356): `src.outline`.

The same field the map draws (`frontend/src/panels/map/territory.ts`), and
the same fixture as the map's test (`territory.test.ts`, "the line the engine
reads"): the capital as the vault lays it and D-332's ring, asked the same
points. The two trees cannot import each other, and they meet in these
numbers -- a change to the field on one side fails the other's copy.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from src import outline
from src.constants import Constants

#: The test's world, as the map's test has it.
R_M = 319_000.0
#: The vault's numbers (`city.outline_*`).
LAW = outline.OutlineLaw(
    power=4.0,
    reach_share=0.5,
    lone_reach_m=6.0,
    cells_per_step=8.0,
    max_cells=160.0,
    bridge_cells=1.5,
)
DEG = 180 / math.pi / R_M

CAPITAL = [
    outline.Member("terra.capital.core", 32.9035, -105.884431, 120),
    outline.Member("terra.capital.library", 32.904936885, -105.888709548, 200),
    outline.Member("terra.capital.market", 32.9035, -105.882077799, 200),
    outline.Member("terra.capital.hall", 32.906194159, -105.884431, 180),
    outline.Member("terra.capital.forge", 32.901344673, -105.875659977, 260),
    outline.Member("terra.capital.pit", 32.897213629, -105.889779185, 300),
    outline.Member("terra.capital.gate", 32.901165062, -105.886356347, 80),
    outline.Member("terra.capital.port", 32.907810654, -105.881863871, 240),
    outline.Member("terra.capital.jail", 32.899907788, -105.89427166, 120),
]
STREETS = [
    ("terra.capital.core", "terra.capital.library"),
    ("terra.capital.core", "terra.capital.market"),
    ("terra.capital.core", "terra.capital.hall"),
    ("terra.capital.library", "terra.capital.market"),
    ("terra.capital.library", "terra.capital.hall"),
    ("terra.capital.market", "terra.capital.forge"),
    ("terra.capital.core", "terra.capital.gate"),
    ("terra.capital.core", "terra.capital.port"),
    ("terra.capital.gate", "terra.capital.jail"),
    ("terra.capital.pit", "terra.capital.gate"),
]
#: The oil field lies inside, three metres in; the coal pit and the
#: floodplain -- where a newcomer makes the first axe (D-196) -- outside.
PROBES = [
    ("oilfield", 32.899548567, -105.885714564, True),
    ("coal", 32.895417523, -105.894913442, False),
    ("floodplain", 32.892184533, -105.892346314, False),
    ("between core and market", 32.9035, -105.883361363, True),
    ("far east", 32.9035, -105.84164552, False),
    ("under the port", 32.908888318, -105.881863871, True),
    ("north of the hall", 32.914276636, -105.884431, False),
]
RING = [
    outline.Member("n0", 0, 0.107766356, 100),
    outline.Member("n1", 0.069270879, 0.082553819, 100),
    outline.Member("n2", 0.106129143, 0.018713431, 100),
    outline.Member("n3", 0.093328402, -0.053883178, 100),
    outline.Member("n4", -0.093328402, -0.053883178, 100),
]
CHORDS = [("n0", "n1"), ("n1", "n2"), ("n2", "n3"), ("n3", "n4"), ("n4", "n0")]
#: The middle of the ring, the ground west of its open chord, and a point
#: well beyond it: open without the ways, closed with them.
RING_PROBES = [
    (0, 0, False, True),
    (0, -0.071844238, False, True),
    (0.269415891, 0, False, False),
]


def _line(members, ways=()) -> outline.Outline:
    drawn = outline.outline_of(members, ways, R_M, LAW)
    assert drawn is not None
    return drawn


@pytest.mark.parametrize(("name", "lat", "lon", "expected"), PROBES)
def test_the_capital_covers_its_ground_and_not_the_fields_beyond(
    name: str, lat: float, lon: float, expected: bool
) -> None:
    assert _line(CAPITAL, STREETS).covers(lat, lon) is expected, name


def test_a_ring_its_ways_close_covers_its_middle() -> None:
    open_ring = _line(RING)
    closed = _line(RING, CHORDS)
    for lat, lon, when_open, when_closed in RING_PROBES:
        assert open_ring.covers(lat, lon) is when_open
        assert closed.covers(lat, lon) is when_closed


def test_every_node_of_the_frame_is_within_its_own_line() -> None:
    line = _line(CAPITAL, STREETS)
    for member in CAPITAL:
        assert line.covers(member.lat, member.lon), member.key


def test_the_line_does_not_follow_the_order_the_rows_come_in() -> None:
    #: Four corners of a square: every gap to a neighbour is the same, and the
    #: tree would otherwise take whichever came first.
    square = [
        outline.Member("d", 0, 0, 100),
        outline.Member("a", 0, 90 * DEG, 100),
        outline.Member("c", 90 * DEG, 0, 100),
        outline.Member("b", 90 * DEG, 90 * DEG, 100),
    ]
    one = _line(square)
    other = _line(list(reversed(square)))
    assert one.values.shape == other.values.shape
    assert (one.values == other.values).all()


def test_a_way_to_anybody_else_is_not_a_street() -> None:
    astray = _line(RING, [("n3", "elsewhere")])
    assert not astray.covers(0, 0)


def test_nothing_outside_the_raster_is_land() -> None:
    line = _line(CAPITAL, STREETS)
    (south, north), (west, east) = line.window()
    assert south < 32.9035 < north and west < -105.884431 < east
    assert not line.covers(north + 1, -105.884431)
    assert not line.covers(32.9035, east + 1)


def test_a_lone_node_holds_its_own_land() -> None:
    lone = _line([outline.Member("solo", 0, 0, 400)])
    #: Its own land reaches sqrt(400 / pi), about eleven metres.
    assert lone.covers(0, 5 * DEG)
    assert not lone.covers(0, 30 * DEG)


def _raster(values: list[list[float]]) -> outline.Outline:
    """A raster laid by hand on a plane of one metre per degree, cell one."""
    field = np.array(values, dtype=np.float64)
    return outline.Outline(
        lat0=0, lon0=0, stretch=1, per_deg=1, x0=0, y0=0, cell=1,
        values=field, open=outline._reached(field),
    )  # fmt: skip


def _pocket(side: float) -> list[list[float]]:
    """Land everywhere but a corner of the border and one sample inside, the
    two touching only across a saddle cell whose other corners are `side`."""
    rows = [[2.0] * 5 for _ in range(5)]
    rows[0][0] = rows[1][1] = 0.0
    rows[0][1] = rows[1][0] = side
    return rows


def test_a_saddle_goes_with_its_middle_as_the_map_splits_it() -> None:
    """The pocket at (1, 1) reaches the open only across the saddle of the
    corner cell. Its middle below the level joins the two outside corners, as
    the map's trace splits it (`marchingSquares`): the pocket is open, not
    land. Its middle at the level or above joins the inside corners, and the
    pocket is a hole -- the city's."""
    assert not _raster(_pocket(1.2)).covers(1.1, 1.1)
    assert _raster(_pocket(2.0)).covers(1.1, 1.1)


def test_in_a_saddle_the_point_belongs_to_its_own_corner() -> None:
    """Across a saddle whose middle is land, the two outside corners are two
    parts: one the open, one a hole. A point near the hole's corner is land,
    one near the open corner is not -- asking whether any corner is open
    would have given both away."""
    line = _raster(_pocket(2.0))
    assert line.covers(0.9, 0.9)
    assert not line.covers(0.1, 0.1)


def test_no_frame_no_line() -> None:
    assert outline.outline_of([], [], R_M, LAW) is None


def test_the_numbers_are_the_vaults(constants: Constants) -> None:
    assert outline.law_of(constants) == LAW
