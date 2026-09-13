# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The sky the pure sky tests fly (`test_sky.py`, `test_flyby.py`): the
vault's system written out by hand. Not collected by pytest."""

from __future__ import annotations

from typing import NamedTuple

from src import sky
from src.sky import _base


class Circle(NamedTuple):
    """One world's orbit as the sky reads it: where, how long, and from where."""

    key: str
    radius: float
    period_days: float
    phase: float


#: The seed's system with the vault's own numbers, written out by hand: the
#: arithmetic is tested against the vault's shape, not against its build, and
#: a build read here would make every one of these tests a test of the build.
#: The two numbers a world has are shares of the **Earth's** (D-320, D-324) --
#: mass, which the sky turns into a pull, and radius, which the map's scale
#: turns into ground; the land one walks is neither and is not the sky's.
#:
#: **Kept equal to the vault by a test of their own** (below). They were not,
#: and that is how a world where Pyroxis weighs three hundred Earths was flown
#: here as one where it weighs one and a third: every test in this file passed
#: against a system that no longer existed, the parking circle's among them.
MASS = {"terra": 1.0, "pyroxis": 317.8, "aurora": 1.5, "aquatica": 0.5}
SHARE = {"terra": 1.0, "pyroxis": 10.973, "aurora": 1.393, "aquatica": 0.838}
PLANET_MU = 24.0
BODY_RADIUS = 0.08

#: Where each world circles, written out for the same reason the masses are:
#: this file tests the arithmetic against the vault's **shape**, and a build
#: read here would make every test a test of the build. The radius is not a
#: number the vault keeps -- it follows the year by Kepler (`sky.circle_of`) --
#: and these four triples are checked against it below.
ORBITS = (
    Circle("pyroxis", 72.95, 11.0, 0.80),
    Circle("terra", 136.0, 28.0, 2.10),
    Circle("aquatica", 250.51, 70.0, 4.00),
    Circle("aurora", 378.50, 130.0, 2.28),
)


def system(*, bodies: bool = True) -> sky.System:
    #: The vault's own layout since 2026-09-08 (`sky.circle_of`): the year is
    #: the tuned number and the radius follows it by Kepler.
    circles = {one.key: (one.radius, one.period_days, one.phase) for one in ORBITS}
    mu = _base.astro.mu_of(circles["terra"])
    return sky.System(
        mu=mu,
        bodies=tuple(
            sky.Body(
                key=key, orbit=orbit, mu=PLANET_MU * MASS[key], radius=BODY_RADIUS * SHARE[key]
            )
            for key, orbit in sorted(circles.items())
        )
        if bodies
        else (),
        corona=35.0,
        edge=800.0,
        #: Three of each body's own radii (D-324). Terra's is `BODY_RADIUS`
        #: times a share of one, so its circle is the same 1.5 units these
        #: tests were written against.
        park_radii=3.0,
        #: Twelve of each body's own radii: four times the circle, which is
        #: the room the weakest legal hull needs to brake into it (D-324).
        capture_radii=12.0,
        capture_speed=2.0,
        eject_window=0.15,
        approach=4.0,
        late_leg=0.25,
        dock_radius=0.2,
        dock_speed=0.5,
        sight_radius=5.0,
    )
