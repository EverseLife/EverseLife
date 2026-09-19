# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The flat plan of an inside: points in map units (D-237, D-247).

Outside `engine` on purpose, beside `globe`: this is arithmetic with numbers
of its own -- the half of a segment -- and the rule modules hold none (D-065).
How far a step reaches is the caller's.
"""

from __future__ import annotations

import math

Point = tuple[float, float]


def corners(one: Point, other: Point, reach: float) -> tuple[Point, Point] | None:
    """The two points `reach` from both `one` and `other`, or None where there are none.

    The apexes of the isosceles triangle on the two, one on each side of the
    line between them. None where the two stand on one spot, or further apart
    than two reaches.
    """
    dx, dy = other[0] - one[0], other[1] - one[1]
    apart = math.hypot(dx, dy)
    if apart == 0 or apart > 2 * reach:
        return None
    rise = math.sqrt(reach**2 - (apart / 2) ** 2)
    mid = (one[0] + dx / 2, one[1] + dy / 2)
    left = (mid[0] - rise * dy / apart, mid[1] + rise * dx / apart)
    right = (mid[0] + rise * dy / apart, mid[1] - rise * dx / apart)
    return left, right
