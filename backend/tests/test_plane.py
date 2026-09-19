# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The flat plan's arithmetic: the corners a step from two points."""

from __future__ import annotations

import math

import pytest

from src import plane


def test_the_corners_stand_a_reach_from_both_ends() -> None:
    both = plane.corners((0.0, 0.0), (150.0, 0.0), 150.0)
    assert both is not None
    for spot in both:
        assert math.dist(spot, (0.0, 0.0)) == pytest.approx(150.0)
        assert math.dist(spot, (150.0, 0.0)) == pytest.approx(150.0)
    #: One on each side of the line between the two.
    assert both[0][1] * both[1][1] < 0


def test_there_are_no_corners_too_far_apart_or_on_one_spot() -> None:
    assert plane.corners((0.0, 0.0), (301.0, 0.0), 150.0) is None
    assert plane.corners((5.0, 5.0), (5.0, 5.0), 150.0) is None
    #: Exactly two reaches apart, the two corners meet in the middle.
    touching = plane.corners((0.0, 0.0), (300.0, 0.0), 150.0)
    assert touching is not None
    assert touching[0] == pytest.approx((150.0, 0.0))
