# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The sky as a system of bodies (D-289): the star and the planets that pull
a hull all the way, and the numbers the whole simulation is measured in.

Units are the map's, as in D-271: a length is the vault's orbit radius unit,
a time is a real day, a speed is units per day. The star's gravitational
parameter is read off the planets' orbits (Kepler III), never written down;
a planet's is `orbit.planet_mu` times its share of Terra's gravity.

A hull is a test particle: it is pulled by all five bodies and pulls nothing.
The planets keep to the circles the seed laid -- they do not perturb one
another, and the ship does not perturb them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from src import astro
from src.constants import Constants
from src.constants import registry as R
from src.models.world import Planet

#: A position or a velocity: `(N, 2)` rows of x, y -- every routine here works
#: on a batch, because the slider is forty passages through one integrator.
Rows = np.ndarray


@dataclass(frozen=True, slots=True)
class Body:
    """One planet: where it runs, how hard it pulls, how big it is to hit."""

    key: str
    orbit: astro.Orbit
    mu: float
    #: Closer than this to the centre is the ground, and a hull is lost on it.
    radius: float


@dataclass(frozen=True, slots=True)
class System:
    """The whole sky at once, and the ruler it is measured with."""

    #: The star's parameter, units cubed over days squared.
    mu: float
    bodies: tuple[Body, ...]
    #: Nearer the star than this is the corona: a hull is lost in it (D-271).
    corona: float
    #: Farther than this is out of the system: nobody reaches a hull there.
    edge: float
    #: The parking circle round a planet (D-289): where a moored hull runs.
    park: float
    #: The window the autopilot puts a hull on the circle in: this close, this
    #: nearly at the circle's speed.
    capture_radius: float
    capture_speed: float
    #: Inside this many parking radii the helm matches the circle whatever
    #: its speed; and the shortest leg it lays once the planned hour has
    #: passed without a capture, days.
    approach: float
    late_leg: float

    def body(self, key: str) -> Body:
        for one in self.bodies:
            if one.key == key:
                return one
        raise KeyError(key)


#: delta-v below this is nothing to pay for, units a day: the last digit of the
#: arithmetic must not demand a stack.
DV_EPS = 1e-6
#: A slice of time below this is no slice: the loop's own last digit.
TIME_EPS = 1e-12
#: The bearing hash: a hull is put on its circle at an angle spun off its
#: id, so two hulls over one planet do not sit at one point.
_BEARING_HEX = 8
_BEARING_MOD = 997


def bearing(hex_id: str) -> float:
    """An angle on the circle, steady per id, radians."""
    return (int(hex_id[:_BEARING_HEX], 16) % _BEARING_MOD) / _BEARING_MOD * 2 * math.pi


def system_of(constants: Constants, orbits: dict[Planet, astro.Orbit]) -> System:
    """The system as the vault and the seed describe it. One reading per command."""
    #: A sky without planets -- a test world laid without spheres -- is a
    #: system with no bodies: nothing to offer, nothing to fall onto, and
    #: nothing to raise about.
    if not orbits:
        return System(
            mu=0.0,
            bodies=(),
            corona=float(constants[R.ORBIT_CORONA_RADIUS]),
            edge=float(constants[R.ORBIT_SYSTEM_RADIUS]),
            park=float(constants[R.ORBIT_PARK_RADIUS]),
            capture_radius=float(constants[R.ORBIT_CAPTURE_RADIUS]),
            capture_speed=float(constants[R.ORBIT_CAPTURE_SPEED]),
            approach=float(constants[R.ORBIT_APPROACH_RADII]),
            late_leg=float(constants[R.ORBIT_LATE_LEG_DAYS]),
        )
    first = next(iter(orbits.values()))
    gravity = constants[R.PLANET_GRAVITY]
    radii = constants[R.PLANET_RADIUS]
    planet_mu = float(constants[R.ORBIT_PLANET_MU])
    bodies = tuple(
        Body(
            key=planet.value,
            orbit=orbit,
            mu=planet_mu * float(gravity.get(planet.value, 1.0)),
            radius=float(radii.get(planet.value, 0.0)),
        )
        for planet, orbit in sorted(orbits.items(), key=lambda pair: pair[0].value)
    )
    return System(
        mu=astro.mu_of(first),
        bodies=bodies,
        corona=float(constants[R.ORBIT_CORONA_RADIUS]),
        edge=float(constants[R.ORBIT_SYSTEM_RADIUS]),
        park=float(constants[R.ORBIT_PARK_RADIUS]),
        capture_radius=float(constants[R.ORBIT_CAPTURE_RADIUS]),
        capture_speed=float(constants[R.ORBIT_CAPTURE_SPEED]),
        approach=float(constants[R.ORBIT_APPROACH_RADII]),
        late_leg=float(constants[R.ORBIT_LATE_LEG_DAYS]),
    )


def place(body: Body, t: np.ndarray | float) -> tuple[Rows, Rows]:
    """Where the planet stands and how it moves at `t` -- for a batch of times."""
    radius, period, phase = body.orbit
    #: Always rows, one per time: a single moment is a batch of one, so every
    #: caller indexes the same way.
    angle = phase + 2 * np.pi * np.atleast_1d(np.asarray(t, dtype=float)) / period
    speed = 2 * np.pi * radius / period
    r = np.stack([radius * np.cos(angle), radius * np.sin(angle)], axis=-1)
    v = np.stack([-speed * np.sin(angle), speed * np.cos(angle)], axis=-1)
    return r, v


def circle_speed(body: Body, radius: float) -> float:
    """The speed of a circular orbit round the planet at this radius."""
    return float(np.sqrt(body.mu / radius))


def circle_rate(body: Body, radius: float) -> float:
    """How fast the parking circle turns, radians a day."""
    return circle_speed(body, radius) / radius


def parking(system: System, body: Body, t: float, phase: float) -> tuple[Rows, Rows]:
    """A hull moored on the parking circle at `phase` (its own angle round the
    planet), as a heliocentric state at `t`: the planet's place and speed plus
    the circle's. One row."""
    r_p, v_p = place(body, t)
    speed = circle_speed(body, system.park)
    r = r_p + system.park * np.array([[np.cos(phase), np.sin(phase)]])
    v = v_p + speed * np.array([[-np.sin(phase), np.cos(phase)]])
    return r, v


def norms(rows: Rows) -> np.ndarray:
    """The length of every row, shape `(N,)`."""
    return np.sqrt(np.sum(rows * rows, axis=-1))
