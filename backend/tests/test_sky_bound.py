# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A hull near a planet, in the arithmetic alone (D-354).

Pinned is what lets a hull hang over a world with no node under it:

* inside a planet's inner sphere the other worlds pull as a tide, so a circle flown
  free stays a circle -- without it Pyroxis drags a hull off Terra's circle
  and into the ground in four to eleven days;
* out in the deep the pulls are the full ones, as an interplanetary arc
  must feel them;
* a closed orbit round a planet is read by Kepler's arithmetic, exact on
  the circle and on a steep ellipse alike, and only where over the hours
  between two restamps it reads what the integrator flies;
* only a closed orbit that keeps half a radius over the ground and within a
  fifth of the Hill radius counts as one -- the rest is flown, and may well
  fall.
"""

from __future__ import annotations

import numpy as np
import pytest

from sky_kit import system as _system
from src import astro, sky
from src.constants import Constants
from src.constants import registry as R
from src.sky import _base, bound, field

#: How long a Kepler reading is trusted before the tick flies the stamp on
#: under the whole sky: the vault's `orbit.restamp_hours`, checked below.
RESTAMP_HOURS = 6.0


def _ellipse(
    body: sky.Body, t0: float, near: float, far: float, turn: float = 0.0
) -> tuple[tuple[float, float], tuple[float, float]]:
    """A hull at the near point of an ellipse round `body`, prograde, the
    near point `turn` radians round from the planet's +x."""
    p, vp = _base.place(body, t0)
    speed = float(np.sqrt(body.mu * (2 / near - 2 / (near + far))))
    out = np.array([np.cos(turn), np.sin(turn)])
    along = np.array([-np.sin(turn), np.cos(turn)])
    r = p[0] + near * out
    v = vp[0] + speed * along
    return (float(r[0]), float(r[1])), (float(v[0]), float(v[1]))


def _lowest(system: sky.System, rows, days: float) -> list[float]:
    """How close to its planet's centre each `(body, r0, v0)` comes in `days`
    under the whole sky, from sky day nought, the ground asked at every step."""
    low = np.full(len(rows), np.inf)
    radius = np.array([body.orbit[0] for body, _, _ in rows])
    phase = np.array([body.orbit[2] for body, _, _ in rows])
    year = np.array([body.orbit[1] for body, _, _ in rows])

    def watch(tt: np.ndarray, rr: np.ndarray, _vv: np.ndarray) -> None:
        angle = _base.turned(phase, year, tt)
        gap = np.hypot(rr[:, 0] - radius * np.cos(angle), rr[:, 1] - radius * np.sin(angle))
        np.minimum(low, gap, out=low)

    field.advance(
        system,
        np.zeros(len(rows)),
        np.full(len(rows), days),
        np.array([r0 for _, r0, _ in rows]),
        np.array([v0 for _, _, v0 in rows]),
        dt_max=1 / 24,
        watch=watch,
    )
    return [float(one) for one in low]


def _radius_drift(system: sky.System, days: float) -> dict[str, float]:
    """Every world's parking circle flown free under the whole sky, hour by
    hour: the worst share by which the hull's distance to its planet left
    the circle's radius."""
    rows = [sky.parking(system, body, 0.0, 0.3) for body in system.bodies]
    r = np.vstack([one for one, _ in rows])
    v = np.vstack([one for _, one in rows])
    t = np.zeros(len(rows))
    worst = dict.fromkeys((body.key for body in system.bodies), 0.0)
    for hour in range(1, int(days * 24) + 1):
        until = np.full(len(rows), hour / 24)
        r, v = field.advance(system, t, until, r, v, dt_max=1 / 24)
        t = until
        for i, body in enumerate(system.bodies):
            p, _ = _base.place(body, t[i])
            gap = float(np.hypot(*(r[i] - p[0])))
            worst[body.key] = max(worst[body.key], abs(gap / sky.park_of(system, body) - 1))
    return worst


def test_a_circle_flown_free_keeps_to_its_planet() -> None:
    """Three days of every world's circle under all five bodies stay within
    a per cent of the circle.

    Without the tide Terra's circle is 36 % off in these three days and in
    the ground on the fifth, Aquatica's 4.6 % and Aurora's 3 %: the planets
    ride their circles and do not feel one another, so Jupiter-heavy Pyroxis
    pulled the hull and not the world it circles. Pyroxis' own circle moves
    by a quarter of a per cent either way -- the star's tide, which is real.
    """
    worst = _radius_drift(_system(), days=3.0)
    for key, drift in worst.items():
        assert drift < 0.01, f"{key}: круг ушёл на {drift:.1%}"


def test_out_in_the_deep_the_pulls_are_the_full_ones() -> None:
    """Far from every world a hull feels each of them whole -- the tide is a
    correction for a hull that falls with its planet, and an interplanetary
    arc falls with none."""
    system = _system()
    here = np.array([[0.0, 200.0]])
    t = np.array([3.0])
    want = -system.mu * here / np.linalg.norm(here) ** 3
    for body in system.bodies:
        p, _ = _base.place(body, 3.0)
        d = here - p
        assert np.linalg.norm(d) > _base.hill_of(system, body), body.key
        want = want - body.mu * d / np.linalg.norm(d) ** 3
    assert field.pull(system, t, here) == pytest.approx(want, rel=1e-12)


@pytest.mark.parametrize("eccentricity", [0.0, 1e-9, 0.3, 0.9, 0.99])
def test_kepler_is_the_orbit_to_the_last_digit(eccentricity: float) -> None:
    """The batched Kepler against the universal-variable propagation it
    stands beside (`astro.propagate`): the circle needs nothing to divide
    by, a steep ellipse converges from half a turn, and a month of laps
    brings the hull back where the arithmetic says."""
    mu, axis = 24.0, 0.5
    near = axis * (1 - eccentricity)
    fast = float(np.sqrt(mu * (1 + eccentricity) / near))
    start = astro.propagate(mu, (near, 0.0), (0.0, fast), 0.37 * astro.lap(mu, axis))
    lap = astro.lap(mu, axis)
    for dt in (0.0, 0.013, 0.4 * lap, 1.9 * lap, 30.0):
        want = astro.propagate(mu, *start, dt % lap) if dt % lap else start
        r, v = bound.kepler(
            np.array([mu]), np.array([start[0]]), np.array([start[1]]), np.array([dt])
        )
        assert r[0] == pytest.approx(want[0], abs=1e-8 * axis)
        assert v[0] == pytest.approx(want[1], abs=1e-8 * fast)


def test_kepler_reads_only_what_the_integrator_would_fly_there() -> None:
    """Every orbit Kepler is let read (`sky.kepler_reads`) -- circles and
    ellipses from the ground to the edge of the bound ones, in four
    orientations at four places of the sky -- ends a restamp's hours within
    a hundredth of the meeting distance of where the integrator flies it
    under the whole sky.

    And the parking circles fall where they should: Terra's, Aquatica's and
    Aurora's are read, Pyroxis' is not -- close to the star, its circle
    swings a quarter of a per cent under the star's tide, and over six hours
    Kepler would place a hull there a quarter of the meeting distance off.
    """
    system = _system()
    window = RESTAMP_HOURS / 24
    read: list[tuple[sky.Bound, tuple[float, float], tuple[float, float]]] = []
    for body in system.bodies:
        park = sky.park_of(system, body)
        circle = sky.bound_to(system, 0.0, *_ellipse(body, 0.0, park, park))
        assert circle is not None
        assert sky.kepler_reads(system, circle, window) is (body.key != "pyroxis"), body.key
        edge = sky.hill_of(system, body) * sky.STABLE_SHARE
        for t0 in (0.0, 4.0, 9.0, 15.0):
            for turn in (0.0, 0.5 * np.pi, np.pi, 1.5 * np.pi):
                for near in np.geomspace(sky.GROUND_MARGIN * body.radius, 0.95 * edge, 5):
                    for stretch in (1.0, 2.5):
                        state = _ellipse(body, t0, near, min(near * stretch, 0.99 * edge), turn)
                        held = sky.bound_to(system, t0, *state)
                        if held is not None and sky.kepler_reads(system, held, window):
                            read.append((held, *state))
    assert {held.body.key for held, _, _ in read} == {"terra", "aquatica", "aurora"}
    starts = np.array([held.t0 for held, _, _ in read])
    flown, _ = field.advance(
        system,
        starts,
        starts + window,
        np.array([r0 for _, r0, _ in read]),
        np.array([v0 for _, _, v0 in read]),
        dt_max=1 / 24,
    )
    kept, _ = sky.bound_states([held for held, _, _ in read], starts + window)
    gaps = np.hypot(*(kept - flown).T)
    worst = int(np.argmax(gaps))
    assert gaps[worst] < bound.KEPLER_SLACK * system.dock_radius, (
        read[worst][0].body.key,
        read[worst][0].far,
        float(gaps[worst]),
    )


def test_only_a_closed_orbit_off_the_ground_and_in_the_inner_sphere_is_bound() -> None:
    """The circle is bound; so is nothing that leaves, strikes the ground, or
    reaches past the fifth of the Hill radius measured to keep."""
    system = _system()
    terra = system.body("terra")
    park = sky.park_of(system, terra)
    p, vp = _base.place(terra, 0.0)
    circle = sky.circle_speed(terra, park)

    def moving(speed: float, at: float = park) -> sky.Bound | None:
        return sky.bound_to(system, 0.0, (p[0, 0] + at, p[0, 1]), (vp[0, 0], vp[0, 1] + speed))

    assert moving(circle) is not None
    #: Past escape speed: leaving.
    assert moving(1.5 * circle) is None
    #: So slow the ellipse's near point is under the ground.
    assert moving(0.3 * circle) is None
    #: Closed, but its far point is past a fifth of the Hill radius; and
    #: the same circle just inside it, bound.
    edge = sky.hill_of(system, terra) * sky.STABLE_SHARE
    near_edge = sky.bound_to(
        system,
        0.0,
        (p[0, 0] + 0.9 * edge, p[0, 1]),
        (vp[0, 0], vp[0, 1] + sky.circle_speed(terra, 0.9 * edge)),
    )
    assert near_edge is not None
    far = sky.bound_to(
        system,
        0.0,
        (p[0, 0] + 0.9 * edge, p[0, 1]),
        (vp[0, 0], vp[0, 1] + 1.1 * sky.circle_speed(terra, 0.9 * edge)),
    )
    assert far is None


def test_a_wide_ellipse_is_flown_not_taken_on_trust() -> None:
    """An ellipse round Terra from just above the ground out to three tenths
    of the Hill radius used to be called stable by arithmetic -- it was
    inside the inner sphere -- and the star's tide pumps it into the ground
    on the fourth day. It is not bound any more, the coast is flown, and the
    forecast says what the sky will do."""
    system = _system()
    terra = system.body("terra")
    hill = sky.hill_of(system, terra)
    wide = _ellipse(terra, 0.0, 0.02 * hill, 0.3 * hill)
    assert sky.bound_to(system, 0.0, *wide) is None
    fate = sky.inertia(system, 0.0, *wide, horizon=10.0, dt_max=1 / 24)
    assert fate.kind == sky.CRASH and fate.body == "terra", fate.kind
    assert 2.0 < fate.at < 6.0, fate.at


def test_the_corners_of_the_bound_orbits_hold() -> None:
    """The far corner of what is called bound -- the near point half a
    radius over the ground, the far point at a fifth of the Hill radius --
    keeps twenty days under the whole sky on every world, the near point
    never down to the ground. A near point skimming the ground under the
    same far point is not bound: the tides move the near point by more than
    it has to spare.

    The bounds were measured on the sky the vault gives today (D-354): a
    change to the masses, radii or years of the worlds is a reason to
    measure them again, and this is the test that says so first.
    """
    system = _system()
    rows = []
    for body in system.bodies:
        far = 0.99 * sky.STABLE_SHARE * sky.hill_of(system, body)
        corner = _ellipse(body, 0.0, 1.01 * sky.GROUND_MARGIN * body.radius, far)
        assert sky.bound_to(system, 0.0, *corner) is not None, body.key
        skimming = _ellipse(body, 0.0, 1.1 * body.radius, far)
        assert sky.bound_to(system, 0.0, *skimming) is None, body.key
        rows.append((body, *corner))
    for body, low in zip(system.bodies, _lowest(system, rows, days=20.0), strict=True):
        assert low > body.radius, f"{body.key}: {low / body.radius:.3f} радиуса"


def test_the_restamp_here_is_the_vaults_own(constants: Constants) -> None:
    """The window the reading is measured over is the one the tick keeps."""
    assert float(constants[R.ORBIT_RESTAMP_HOURS]) == RESTAMP_HOURS
