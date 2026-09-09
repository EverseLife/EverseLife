# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The ground between the eye and the place (landscape plan, wave 9, §10).

What is pinned:

* the geometry, on a slope drawn by hand: a flat plain hides nothing, a rise
  between hides, the same rise seen from above it does not, and the curve of
  the planet is carried rather than assumed away;
* the ladder of the horizon the plan reckoned with -- 630 m from a standing
  man on Terra, 24 km from the highest summit;
* the rule the owner set: the ground may take from the sight radius and never
  adds to it;
* and that the rule bites on the real planet, in the country it should --
  a neighbour a hundred metres off is hidden in the mountains and never on
  the steppe.
"""

from __future__ import annotations

import math

import pytest

from src import globe
from src.constants import Constants
from src.constants import registry as R
from src.engine import biome, horizon, terrain
from src.models.world import Planet
from src.units import METRES_PER_KM

#: A round planet of Terra's own size, for the geometry without the field.
TERRA_R = 99_546.875


def test_the_drop_is_the_curve_and_not_a_guess() -> None:
    """Half a metre at ten kilometres, five centimetres at a hundred metres:
    the sagitta, which is why a distant shore stands lower than a near one."""
    assert horizon.drop_m(TERRA_R, 10_000) == pytest.approx(502.3, abs=0.1)
    assert horizon.drop_m(TERRA_R, 100) == pytest.approx(0.05, abs=0.01)
    assert horizon.drop_m(TERRA_R, 0) == 0


def test_the_horizon_is_the_ladder_the_plan_reckoned_with() -> None:
    """§10.1: the reason the world is unknown is that the horizon of a man on
    this planet is six hundred metres, and height is what buys more."""
    ladder = {2: 631, 50: 3_155, 200: 6_312, 1_000: 14_115, 3_000: 24_496}
    for eye, far in ladder.items():
        assert horizon.horizon_m(TERRA_R, eye) == pytest.approx(far, rel=0.01)
    #: Nothing below the sea, and no eye at all sees nothing.
    assert horizon.horizon_m(TERRA_R, 0) == 0
    assert horizon.horizon_m(TERRA_R, -5) == 0


def test_a_plain_hides_nothing_and_a_rise_between_hides() -> None:
    flat = [(along, 0.0) for along in range(10, 100, 10)]
    #: An eye two metres up, a place at its feet a hundred metres off.
    assert not horizon.blocked(TERRA_R, 100, 2, 0, flat)
    #: A four-metre rise halfway is over the line of sight and cuts it.
    rise = [*flat[:4], (50.0, 4.0), *flat[5:]]
    assert horizon.blocked(TERRA_R, 100, 2, 0, rise)
    #: The same rise seen from above it is not in the way.
    assert not horizon.blocked(TERRA_R, 100, 12, 0, rise)
    #: Nor is it, when what is looked at stands higher than the rise.
    assert not horizon.blocked(TERRA_R, 100, 2, 9, rise)


def test_the_ground_beyond_the_place_is_not_between() -> None:
    """A wall behind the target hides nothing: only what is on the way is."""
    beyond = [(50.0, 0.0), (100.0, 900.0), (150.0, 900.0)]
    assert not horizon.blocked(TERRA_R, 100, 2, 0, beyond)
    #: And a place at the eye's own point is always seen.
    assert not horizon.blocked(TERRA_R, 0, 2, 0, [(0.0, 900.0)])


def test_the_curve_alone_hides_what_a_flat_world_would_show() -> None:
    """Ten kilometres over a sea at nought: on a flat world the line of sight
    would run level and the far shore would be in it; on this one the water
    itself stands five hundred metres over it, and that is the horizon."""
    sea = [(along, 0.0) for along in range(1_000, 10_000, 1_000)]
    assert horizon.blocked(TERRA_R, 10_000, 2, 0, sea)
    #: From high enough the same shore comes back: the horizon is a height.
    assert not horizon.blocked(TERRA_R, 10_000, 600, 0, sea)


def test_the_reach_is_the_radius_the_ground_may_only_shorten(constants: Constants) -> None:
    """The owner's rule (2026-09-09): the vault's radius is the ceiling, and
    the curve of the planet may take from it and never add."""
    radius = float(constants[R.MAP_SIGHT_KM]) * METRES_PER_KM
    field = terrain.field_of(constants, Planet.TERRA)
    for lat in range(-60, 61, 15):
        for lon in range(-180, 180, 45):
            if field.is_water(lat, lon):
                continue
            assert horizon.reach_m(constants, Planet.TERRA, (lat, lon)) <= radius
    #: On this planet the ceiling is what binds everywhere on land: a
    #: hundred metres against a horizon of six hundred and up.
    assert horizon.reach_m(constants, Planet.TERRA, (0.0, 0.0)) <= radius


def test_the_same_two_points_are_hidden_or_seen_for_ever(constants: Constants) -> None:
    """The field is a file (D-237): sight is a reading, and a reading of the
    same two points does not change between one look and the next."""
    field = terrain.field_of(constants, Planet.TERRA)
    radius = globe.radius_m(constants, Planet.TERRA)
    seen = 0
    for lat in range(-40, 41, 20):
        for lon in range(-160, 161, 40):
            if field.is_water(lat, lon):
                continue
            here = (float(lat), float(lon))
            there = globe.offset(radius, here, 60.0, 60.0)
            once = horizon.hidden(constants, Planet.TERRA, here, there)
            assert once == horizon.hidden(constants, Planet.TERRA, here, there)
            seen += 1
    assert seen > 5


def test_broken_country_hides_and_flat_country_hardly_does(constants: Constants) -> None:
    """The rule has to bite where the ground is broken and next to nowhere
    else, or it is a formula that never changes an answer.

    A share rather than a count: what the mountains do to sight is only worth
    saying against what the plains do, and the two are measured by the same
    sweep in the same run -- the numbers of one build of the field are not a
    thing to write into a test.
    """
    field = terrain.field_of(constants, Planet.TERRA)
    radius = globe.radius_m(constants, Planet.TERRA)
    reach = float(constants[R.MAP_SIGHT_KM]) * METRES_PER_KM
    hidden: dict[str, int] = {}
    tried: dict[str, int] = {}
    #: Four ways out of every point of a coarse grid over the land: enough of
    #: the mountains to count, few enough readings to run in a second.
    for lat in range(-60, 61, 4):
        for lon in range(-180, 180, 4):
            if field.is_water(lat, lon):
                continue
            where = biome.classify(constants, Planet.TERRA, lat, lon)
            if where is None:
                continue
            here = (float(lat), float(lon))
            for point in range(globe.COMPASS_POINTS // 2):
                turn = math.tau * point / (globe.COMPASS_POINTS // 2)
                there = globe.offset(radius, here, reach * math.sin(turn), reach * math.cos(turn))
                tried[where] = tried.get(where, 0) + 1
                if horizon.hidden(constants, Planet.TERRA, here, there):
                    hidden[where] = hidden.get(where, 0) + 1

    def share(names: tuple[str, ...]) -> float:
        return sum(hidden.get(one, 0) for one in names) / sum(tried[one] for one in names)

    broken = ("alpine", "foothills")
    open_country = tuple(one for one in tried if one not in broken)
    assert tried.get("alpine", 0) > 60 and open_country
    #: The open country as a whole, not one biome of it: which of them a
    #: planet happens to have much of is the seed's business, and a single
    #: name can come out too thin to mean anything -- Terra's steppe is
    #: twenty-eight readings of four thousand.
    assert sum(tried[one] for one in open_country) > 600
    #: The mountains hide a real part of what stands a hundred metres off,
    #: and they hide several times what the open country does.
    assert share(("alpine",)) > 0.02
    assert share(("alpine",)) > 3 * share(open_country)
