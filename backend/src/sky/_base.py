# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The sky as a system of bodies (D-289): the star and the planets that pull
a hull all the way, and the numbers the whole simulation is measured in.

Units are the map's, as in D-271: a length is the vault's orbit radius unit,
a time is a real day, a speed is units per day. The star's gravitational
parameter is read off the planets' orbits (Kepler III), never written down;
a planet's is `orbit.planet_mu` times its share of Terra's mass (D-320 --
its share of Terra's *gravity* is what this used to say, and the two are not
the same number once the worlds differ in size).

A hull is a test particle: it is pulled by all five bodies and pulls nothing.
The planets keep to the circles the seed laid -- they do not perturb one
another, and the ship does not perturb them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from src import astro
from src.constants import ConstantError, Constants
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
    #: The parking circle round a planet (D-289), in radii of that planet's
    #: own drawn body (D-324): `park_of` turns it into units. One flat number
    #: of units for the whole system stopped working the day the worlds became
    #: honest sizes -- Pyroxis is drawn eleven Terra radii across, and a circle
    #: of a flat one and a half units round it runs underground.
    park_radii: float
    #: The window the autopilot puts a hull on the circle in: this close, this
    #: nearly at the circle's speed. In radii of the body arrived at, like the
    #: circle itself (D-324) -- `capture_of` turns it into units.
    capture_radii: float
    capture_speed: float
    #: The ejection window, radians (D-316).
    eject_window: float
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


#: What the vault gives a world it forgot to describe: Terra's twin, in both
#: numbers at once. A missing line must not make a world free to leave, and it
#: must not make one a point of no size either -- and above all the two must
#: come from the same guess. The older shape defaulted the pull to Terra's and
#: the radius to zero, which is a world of infinite density: exactly the kind
#: of self-contradiction D-320 exists to remove.
LIKE_TERRA = 1.0


def shape_of(constants: Constants, key: str) -> tuple[float, float]:
    """A world's mass and radius, both as shares of Terra's (D-320).

    The one place either is read. Everything a planet does to a ship follows
    from the pair -- what it pulls with at its surface (`mass / radius^2`),
    what it pulls with out here (`orbit.planet_mu` times the mass), how dense
    it is -- and a reader that took one without the other is how the vault
    came to hold three numbers tied by two (OQ-138).
    """
    mass = float(constants[R.PLANET_MASS].get(key, LIKE_TERRA))
    radius = float(constants[R.PLANET_RADIUS].get(key, LIKE_TERRA))
    if radius <= 0.0:
        #: Not a world at all: every quantity below divides by it, and a
        #: negative one would come back positive through the square and look
        #: like a perfectly good planet.
        raise ConstantError(f"planet.radius: {key} is not a size ({radius})")
    return mass, radius


def circle_of(constants: Constants, planet: str) -> astro.Orbit:
    """Where a world circles the star: radius, year and the phase it started at.

    The **year is the tuned number** (D-271): it decides how fast the sky
    turns and how often two worlds meet. The **radius follows from it** by
    Kepler's third law against Terra's pair, and is not a number anybody sets
    -- the star's pull is read off the orbits (`ship.course.mu_of`), so a
    radius that broke the law would price one and the same passage differently
    by the planet it began at. Writing both by hand is how that law gets
    broken, and until 2026-09-08 both were written by hand in the seed.
    """
    periods = constants[R.ORBIT_PERIOD_DAYS]
    terra_days = float(periods.get(TERRA_KEY, 1.0))
    period = float(periods.get(planet, terra_days))
    if period <= 0.0:
        raise ConstantError(f"orbit.period_days: {planet} is not a year ({period})")
    radius = float(constants[R.ORBIT_TERRA_RADIUS]) * (period / terra_days) ** (2.0 / 3.0)
    phase = float(constants[R.ORBIT_PHASE].get(planet, 0.0))
    return (radius, period, phase)


#: The world the others are measured against: its year is the map's own.
TERRA_KEY = "terra"


def _body_of(
    constants: Constants,
    planet: Planet,
    orbit: astro.Orbit,
    *,
    planet_mu: float,
    scale: float,
) -> Body:
    """One world as the sky sees it: what it pulls with, and the ground it has.

    Both come from the same pair (D-320) -- the pull out here is the mass on
    the vault's scale, and the ground is the radius on the map's.
    """
    mass, radius = shape_of(constants, planet.value)
    return Body(key=planet.value, orbit=orbit, mu=planet_mu * mass, radius=scale * radius)


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
            park_radii=float(constants[R.ORBIT_PARK_RADII]),
            capture_radii=float(constants[R.ORBIT_CAPTURE_RADII]),
            capture_speed=float(constants[R.ORBIT_CAPTURE_SPEED]),
            eject_window=float(constants[R.ORBIT_EJECT_WINDOW]),
            approach=float(constants[R.ORBIT_APPROACH_RADII]),
            late_leg=float(constants[R.ORBIT_LATE_LEG_DAYS]),
            dock_radius=float(constants[R.ORBIT_DOCK_RADIUS]),
            dock_speed=float(constants[R.ORBIT_DOCK_SPEED]),
            sight_radius=float(constants[R.ORBIT_SIGHT_RADIUS]),
        )
    first = next(iter(orbits.values()))
    #: A world is its mass and its radius, both shares of Terra's (D-320):
    #: what it pulls with out here is the one, and the ground a hull can
    #: strike is the other on the map's scale. A world the vault says nothing
    #: about is Terra's twin rather than a point of no size.
    planet_mu = float(constants[R.ORBIT_PLANET_MU])
    body_radius = float(constants[R.ORBIT_BODY_RADIUS])
    bodies = tuple(
        _body_of(constants, planet, orbit, planet_mu=planet_mu, scale=body_radius)
        for planet, orbit in sorted(orbits.items(), key=lambda pair: pair[0].value)
    )
    return System(
        mu=astro.mu_of(first),
        bodies=bodies,
        corona=float(constants[R.ORBIT_CORONA_RADIUS]),
        edge=float(constants[R.ORBIT_SYSTEM_RADIUS]),
        park_radii=float(constants[R.ORBIT_PARK_RADII]),
        capture_radii=float(constants[R.ORBIT_CAPTURE_RADII]),
        capture_speed=float(constants[R.ORBIT_CAPTURE_SPEED]),
        eject_window=float(constants[R.ORBIT_EJECT_WINDOW]),
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


@dataclass(frozen=True)
class Star:
    """The star as a target (D-289, 2026-09-04): the centre of the sky, still.
    What is aimed at is not the star but the circle round it through the
    hull's own place -- the astrocentric orbit, prograde like the planets'."""

    key: str = "star"


STAR = Star()

Target = Body | Drifter | Star


def place_any(target: Target, t: np.ndarray | float) -> tuple[Rows, Rows]:
    """Where a target is and how it moves at `t`: a planet on its circle, a
    drifter on its forecast."""
    if isinstance(target, Drifter):
        return target.state(t)
    if isinstance(target, Star):
        still = np.zeros((np.size(t), 2))
        return still, still
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


def star_circle(system: System, r: np.ndarray | tuple[float, float]) -> np.ndarray:
    """The velocity of the circle round the star through `r`, prograde -- the
    planets' own sense (D-289, 2026-09-04)."""
    pos = np.asarray(r, dtype=float)
    radius = max(float(np.hypot(*pos)), 1e-9)
    around = np.array([-pos[1], pos[0]]) / radius
    return around * float(np.sqrt(system.mu / radius))


def park_of(system: System, body: Body) -> float:
    """The circle a hull parks on round this world, map units (D-324).

    So many of the body's own radii, not so many units of the system: the two
    were the same number while every world was drawn Terra's size, and parted
    the moment they were not. Read wherever the circle is asked about, so a
    hull cannot be aimed at one circle and captured on another.
    """
    return system.park_radii * body.radius


def capture_of(system: System, body: Body) -> float:
    """The window a hull is taken onto this world's circle in, map units.

    Kept beside `park_of` and read the same way: both are measured in the
    body's own radii, and a hull aimed at one circle must not be caught by a
    window drawn round another.
    """
    return system.capture_radii * body.radius


def circle_rate(body: Body, radius: float) -> float:
    """How fast the parking circle turns, radians a day."""
    return circle_speed(body, radius) / radius


def parking(system: System, body: Body, t: float, phase: float) -> tuple[Rows, Rows]:
    """A hull moored on the parking circle at `phase` (its own angle round the
    planet), as a heliocentric state at `t`: the planet's place and speed plus
    the circle's. One row."""
    r_p, v_p = place(body, t)
    park = park_of(system, body)
    speed = circle_speed(body, park)
    r = r_p + park * np.array([[np.cos(phase), np.sin(phase)]])
    v = v_p + speed * np.array([[-np.sin(phase), np.cos(phase)]])
    return r, v


def norms(rows: Rows) -> np.ndarray:
    """The length of every row, shape `(N,)`."""
    return np.sqrt(np.sum(rows * rows, axis=-1))
