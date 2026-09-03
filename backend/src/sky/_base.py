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
    #: The hold (D-289, wave 3): this close and this slow to another hull,
    #: the two fly as one; and how far a foreign hull is seen from.
    dock_radius: float
    dock_speed: float
    sight_radius: float

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
            dock_radius=float(constants[R.ORBIT_DOCK_RADIUS]),
            dock_speed=float(constants[R.ORBIT_DOCK_SPEED]),
            sight_radius=float(constants[R.ORBIT_SIGHT_RADIUS]),
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
        dock_radius=float(constants[R.ORBIT_DOCK_RADIUS]),
        dock_speed=float(constants[R.ORBIT_DOCK_SPEED]),
        sight_radius=float(constants[R.ORBIT_SIGHT_RADIUS]),
    )


@dataclass(frozen=True, slots=True)
class Drifter:
    """A hull coasting on its forecast (D-289, wave 3): a target that moves
    along a known line rather than round the star.

    The line is the forecast the tick wrote on the drifter's row -- points at
    equal steps from `t0` to `t1`, sky days -- and a rendezvous is aimed at
    the point on it the planned hour falls on. Past the line's end the hull
    is carried on along its last stride: the forecast ends where the coast
    ends, or at the horizon, and a meeting beyond it is a meeting with a
    hull that is no longer there.
    """

    key: str
    t0: float
    t1: float
    trace: tuple[tuple[float, float], ...]
    #: A lap round a planet rather than a coast: the line is read modulo its
    #: period, and the hull is always somewhere on it.
    loops: bool = False

    def state(self, t: np.ndarray | float) -> tuple[Rows, Rows]:
        """Where the drifter is and how it moves at `t`, for a batch of times."""
        tt = np.atleast_1d(np.asarray(t, dtype=float))
        if self.loops and self.t1 > self.t0:
            tt = self.t0 + np.mod(tt - self.t0, self.t1 - self.t0)
        points = np.asarray(self.trace, dtype=float)
        if len(points) < 2 or self.t1 <= self.t0:
            r = np.repeat(points[:1] if len(points) else np.zeros((1, 2)), len(tt), axis=0)
            return r, np.zeros_like(r)
        stamps = np.linspace(self.t0, self.t1, len(points))
        stride = (self.t1 - self.t0) / (len(points) - 1)
        vx = np.diff(points[:, 0]) / stride
        vy = np.diff(points[:, 1]) / stride
        #: The stride each moment falls in, clamped to the line's ends: the
        #: velocity is that stride's, and beyond the ends the line goes on
        #: straight at the last stride's speed.
        seg = np.clip(np.searchsorted(stamps, tt, side="right") - 1, 0, len(points) - 2)
        dt = tt - stamps[seg]
        x = points[seg, 0] + vx[seg] * dt
        y = points[seg, 1] + vy[seg] * dt
        return np.stack([x, y], axis=1), np.stack([vx[seg], vy[seg]], axis=1)


Target = Body | Drifter


def place_any(target: Target, t: np.ndarray | float) -> tuple[Rows, Rows]:
    """Where a target is and how it moves at `t`: a planet on its circle, a
    drifter on its forecast."""
    if isinstance(target, Drifter):
        return target.state(t)
    return place(target, t)


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
