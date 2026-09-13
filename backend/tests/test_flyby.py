# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The flyby (D-341): the arithmetic alone, no rows.

Pinned is what the feature stands on:

* the batched Lambert is `astro.lambert`'s own answer, and the integrator
  flies back to where it started when run backwards;
* where the sky lends its pull the slider offers a flyby cheaper than the
  direct arc of the same hour, and where it does not, it offers none;
* no pass is laid under the floor, and past the direct arc's ceiling only a
  passage cheaper than every shorter one is offered;
* the helm flies a flyby through all five bodies, minute by minute, onto the
  target's circle within the promise and never under the floor.
"""

from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np
import pytest

from sky_kit import system
from src import astro, sky
from src.constants import Constants
from src.constants import registry as R
from src.engine.ship import course
from src.sky import assist, field, flyby, lambert
from src.units import HOURS_PER_DAY, MINUTES_PER_HOUR, TRACE_POINTS

#: The slider's grid as the vault sets it (`orbit.slider_from_hours` 2,
#: `orbit.slider_step` 10%): the direct arc's twelve days, and the flyby's
#: own stretch on to forty-five (`orbit.longest_days`,
#: `orbit.flyby_longest_days`). Written out for the reason `sky_kit` is:
#: these tests fly the vault's shape, and one of them checks the numbers.
CEILING = 12 * HOURS_PER_DAY
HOURS = course._grid(2.0, 1.1, CEILING) + tuple(
    one for one in course._grid(2.0, 1.1, 45 * HOURS_PER_DAY) if one > CEILING
)
FLOOR = 2.0
#: A departure the survey of 2026-09-13 found bent round Pyroxis at the fast
#: end: Terra to Aquatica on sky day 126, a little over three days.
SWING = ("terra", "aquatica", 126.0, 74.81)


def _offers(
    world: sky.System, origin: str, goal: str, t0: float, *, floor: float = FLOOR
) -> list[sky.Sample]:
    return sky.flybys(
        world,
        (0.0, 0.0),
        (0.0, 0.0),
        t0,
        world.body(goal),
        HOURS,
        leaving=world.body(origin),
        ceiling=CEILING,
        floor_radii=floor,
    )


def _direct(world: sky.System, origin: str, goal: str, t0: float) -> dict[float, sky.Sample]:
    home = world.body(origin)
    r, v = sky.place(home, t0)
    return {
        one.hours: one
        for one in sky.preview(
            world,
            None,  # type: ignore[arg-type]
            (float(r[0, 0]), float(r[0, 1])),
            (float(v[0, 0]), float(v[0, 1])),
            t0,
            world.body(goal),
            tuple(one for one in HOURS if one <= CEILING),
            leaving=home,
        )
    }


def test_the_batched_lambert_is_astros_own() -> None:
    """Thousands of arcs in one call, each the one `astro.lambert` gives."""
    rng = np.random.default_rng(7)
    count = 400
    world = system()
    a1, a2 = rng.uniform(0, 2 * math.pi, count), rng.uniform(0, 2 * math.pi, count)
    d1, d2 = rng.uniform(60, 400, count), rng.uniform(60, 400, count)
    r1 = np.stack([d1 * np.cos(a1), d1 * np.sin(a1)], axis=1)
    r2 = np.stack([d2 * np.cos(a2), d2 * np.sin(a2)], axis=1)
    tof = np.exp(rng.uniform(math.log(0.05), math.log(45.0), count))
    retro = rng.uniform(size=count) < 0.5
    v1, v2, ok = lambert.arcs(world.mu, r1, r2, tof, retro)
    for i in range(count):
        found = astro.lambert(
            world.mu, tuple(r1[i]), tuple(r2[i]), float(tof[i]), 0, retrograde=bool(retro[i])
        )
        assert bool(found) == bool(ok[i])
        if found:
            ((want1, want2),) = found
            assert v1[i] == pytest.approx(want1, rel=1e-5, abs=1e-6)
            assert v2[i] == pytest.approx(want2, rel=1e-5, abs=1e-6)


def test_the_integrator_flies_back_to_where_it_started() -> None:
    """A row whose end lies before its start is flown backwards (D-341): out
    to a pass and back again is the same place, near Pyroxis as in the deep."""
    world = system()
    pyroxis = world.body("pyroxis")
    p, vp = sky.place(pyroxis, 4.0)
    r0 = p + np.array([[3.0, 0.0], [40.0, 0.0]])
    v0 = vp + np.array([[0.0, 60.0], [0.0, 20.0]])
    r1, v1 = field.advance(world, np.full(2, 4.0), np.full(2, 5.5), r0, v0, dt_max=math.inf)
    back, _ = field.advance(world, np.full(2, 5.5), np.full(2, 4.0), r1, v1, dt_max=math.inf)
    assert np.hypot(*(back - r0).T) == pytest.approx([0.0, 0.0], abs=1e-4)


def test_a_flyby_is_offered_where_the_sky_lends_its_pull() -> None:
    """Terra to Aquatica round Pyroxis: at the fast end of the slider the pass
    is dearer than nothing and cheaper than the straight arc of the same hour,
    and the line drawn for it bends where the pass is."""
    origin, goal, t0, hours = SWING
    world = system()
    offered = {one.hours: one for one in _offers(world, origin, goal, t0)}
    direct = _direct(world, origin, goal, t0)
    assert hours in offered, "у Пироксиса в этот день есть пролёт"
    bent = offered[hours]
    assert bent.via is not None and bent.via.via == "pyroxis"
    assert bent.dv < direct[hours].dv, "пролёт дешевле прямой дуги того же часа"
    assert bent.dv == pytest.approx(bent.dv_out + bent.via.cost + bent.dv_in)
    #: Every offered hour within the direct slider beats its direct arc.
    for one in offered.values():
        if one.hours <= CEILING:
            assert one.dv < direct[one.hours].dv
    #: The kink: the line passes through Pyroxis' place at the pass, and it
    #: still starts at Terra and ends at Aquatica.
    assert len(bent.trace) == TRACE_POINTS
    corner = sky.place(world.body("pyroxis"), t0 + bent.via.at)[0][0]
    assert min(math.dist(point, corner) for point in bent.trace) < 1e-6
    goal_at = sky.place(world.body(goal), t0 + hours / HOURS_PER_DAY)[0][0]
    assert math.dist(bent.trace[-1], goal_at) < 1e-3


def test_no_flyby_is_offered_where_it_does_not_pay() -> None:
    """Pyroxis to Terra never gains by a pass (the survey: 0% of hours), and
    Terra to Pyroxis gains nothing within the direct slider."""
    world = system()
    assert _offers(world, "pyroxis", "terra", 3.0) == []
    assert all(one.hours > CEILING for one in _offers(world, "terra", "pyroxis", 3.0))


def test_no_pass_is_laid_under_the_floor() -> None:
    """Every pass keeps above `orbit.flyby_floor_radii` of the world it goes
    round -- raise the floor and the passes that needed it go or climb -- and
    the conics price a turn sharper than the floor gives as no flyby at all."""
    origin, goal, t0, _ = SWING
    world = system()
    pyroxis = world.body("pyroxis")
    low = _offers(world, origin, goal, t0)
    high = _offers(world, origin, goal, t0, floor=3.0)
    assert any(abs(one.via.rp) < 3.0 * pyroxis.radius for one in low if one.via)
    for floor, offered in ((FLOOR, low), (3.0, high)):
        for one in offered:
            assert one.via is not None
            assert abs(one.via.rp) >= floor * world.body(one.via.via).radius * (1 - 1e-6)
    #: Straight back the way it came: no periapsis above the ground turns that.
    cost, _ = flyby.pass_cost(
        np.array([[30.0, 0.0]]),
        np.array([[-30.0, 0.5]]),
        pyroxis.mu,
        FLOOR * pyroxis.radius,
        flyby.sphere(world, pyroxis),
    )
    assert np.isinf(cost[0])


def test_past_the_ceiling_only_a_cheaper_passage_is_offered() -> None:
    """Past the direct arc's twelve days (D-317) a point of the slider must be
    cheaper than every shorter one: a passage both longer and dearer is no
    choice (D-341)."""
    world = system()
    for origin, goal, t0 in (("terra", "aurora", 127.5), ("terra", "pyroxis", 3.0)):
        offered = _offers(world, origin, goal, t0)
        best = min(one.dv for one in _direct(world, origin, goal, t0).values())
        best = min([best, *(one.dv for one in offered if one.hours <= CEILING)])
        beyond = [one for one in offered if one.hours > CEILING]
        assert beyond, f"{origin}->{goal}: за двенадцатью сутками есть пролёт"
        for one in beyond:
            assert one.dv < best
            best = one.dv
        assert max(one.hours for one in beyond) <= 45 * HOURS_PER_DAY


def test_the_helm_lifts_a_pass_sinking_under_the_floor() -> None:
    """Inside the world's sphere, falling toward a periapsis under the floor,
    the helm burns at full thrust across the fall, whatever the corrections
    last asked for."""
    world = system()
    pyroxis, aquatica = world.body("pyroxis"), world.body("aquatica")
    t = 10.0
    p, vp = sky.place(pyroxis, t)
    #: Ten units out and nearly straight in: the periapsis is in the ground.
    r = (float(p[0, 0]) + 10.0, float(p[0, 1]))
    v = (float(vp[0, 0]) - 30.0, float(vp[0, 1]) + 1.0)
    route = assist.Route(
        via=pyroxis,
        home=None,
        at=t + 0.5,
        rp=3.0,
        aim=(0.0, 0.0),
        burn=0.0,
        arrive=t + 5.0,
        floor=FLOOR * pyroxis.radius,
    )
    leg = assist.Leg(stage=assist.CRUISE, mark=0.3)
    helm, _, want = assist.steer_pass(world, aquatica, route, leg, t, r, v, a_max=500.0, dt=1e-3)
    assert want is None
    assert math.hypot(*helm.thrust) == pytest.approx(500.0)
    #: Across the line to the world, the way the pass goes round.
    assert abs(helm.thrust[0]) < 1e-6 and helm.thrust[1] > 0


class Flown(NamedTuple):
    """How a flyby flown minute by minute ended."""

    captured: bool
    struck: list[str]
    at: float
    closest: float
    spent: float


def _fly(
    world: sky.System,
    target: sky.Body,
    route: assist.Route,
    t0: float,
    r: tuple[float, float],
    v: tuple[float, float],
    *,
    until: float,
    a_max: float,
) -> Flown:
    """The helm's stages and corrections, the five bodies' pull, the tick's
    minute and the ground on every step -- until the mooring or `until`."""
    leg = assist.Leg()
    dt = 1.0 / HOURS_PER_DAY / MINUTES_PER_HOUR
    t, spent, closest = t0, 0.0, math.inf
    struck: list[str] = []

    def watch(tt: np.ndarray, rr: sky.Rows, _vv: sky.Rows) -> None:
        body, gone = sky.ground_of(world, tt, rr)
        if body is not None or gone:
            struck.append(body or "edge")

    while t < until and not struck:
        helm, leg, want = assist.steer_pass(world, target, route, leg, t, r, v, a_max=a_max, dt=dt)
        if want is not None:
            leg = assist.correct(world, target, route, leg, want, t, r, v)
            helm, leg, _ = assist.steer_pass(world, target, route, leg, t, r, v, a_max=a_max, dt=dt)
        if helm.captured:
            return Flown(True, struck, t, closest, spent)
        spent += math.hypot(*helm.thrust) * dt
        rr, vv = field.advance(
            world,
            np.array([t]),
            np.array([t + dt]),
            np.array([r]),
            np.array([v]),
            dt_max=dt,
            thrust=np.array(helm.thrust)[None, :],
            watch=watch,
        )
        r, v, t = (float(rr[0, 0]), float(rr[0, 1])), (float(vv[0, 0]), float(vv[0, 1])), t + dt
        closest = min(closest, math.dist(r, sky.place(route.via, t)[0][0]))
    return Flown(False, struck, t, closest, spent)


def _route(
    world: sky.System, bent: sky.Sample, home: sky.Body | None, t0: float, wait: float
) -> assist.Route:
    assert bent.via is not None
    via = world.body(bent.via.via)
    return assist.Route(
        via=via,
        home=home,
        at=t0 + wait + bent.via.at,
        rp=bent.via.rp,
        aim=bent.via.aim,
        burn=bent.via.burn,
        arrive=t0 + wait + bent.hours / HOURS_PER_DAY,
        floor=FLOOR * via.radius,
    )


def test_the_helm_flies_a_flyby_onto_the_circle_within_the_promise() -> None:
    """The whole flyby on the sky's own terms -- the refined plan, the helm's
    four stages and its corrections, the five bodies' pull, the tick's minute
    -- from a parking circle over Terra onto Aquatica's, past Pyroxis at the
    periapsis the plan named, within the hour the order would promise."""
    origin, goal, t0, hours = SWING
    world = system()
    home, target = world.body(origin), world.body(goal)
    _, vp = sky.place(home, t0)
    phase = math.atan2(float(vp[0, 1]), float(vp[0, 0])) + 1.05
    r0, v0 = sky.parking(world, home, t0, phase)
    r, v = (float(r0[0, 0]), float(r0[0, 1])), (float(v0[0, 0]), float(v0[0, 1]))
    bent = next(one for one in _offers(world, origin, goal, t0) if one.hours == hours)
    assert bent.via is not None
    a_max = 0.5 * 5400.0
    route = _route(world, bent, home, t0, sky.eject_wait(world, target, t0, r, v, bent.v1))
    due = route.arrive + sky.brake_days(world, bent.dv_in, a_max, target)
    flown = _fly(world, target, route, t0, r, v, until=due + 1.0, a_max=a_max)
    assert not flown.struck, f"корпус разбился: {flown.struck}"
    assert flown.captured, "пролёт кончился на круге стоянки Акватики"
    #: The promise holds: the arrival is the arc's hour plus the braking, as
    #: a crossing's (D-316) -- within the tick's own hour of it.
    assert flown.at <= due + 1.0 / HOURS_PER_DAY
    #: The pass the plan named, not one near the ground.
    assert flown.closest >= route.floor
    assert flown.closest == pytest.approx(abs(bent.via.rp), abs=0.1)
    #: The price is the plan's, give or take what finite burns cost at both
    #: ends -- the same looseness a crossing's quote has.
    assert flown.spent <= 1.3 * bent.dv


def test_a_flyby_from_a_drift_leaves_the_world_it_is_held_by() -> None:
    """A hull drifting in Terra's hold, ordered round Pyroxis: no home port,
    and still the departure lasts until the hull is out of Terra's grip with
    its burn given -- a helm that took the drift for open space coasted into
    Terra with the departure never burnt (review of D-341)."""
    origin, goal, t0, _ = SWING
    world = system()
    terra, target = world.body(origin), world.body(goal)
    p, vp = sky.place(terra, t0)
    #: Seven tenths of a unit out, inside Terra's hold of 0.96, going round it.
    gap = 0.7
    around = math.sqrt(terra.mu / gap)
    r = (float(p[0, 0]) + gap, float(p[0, 1]))
    v = (float(vp[0, 0]), float(vp[0, 1]) + around)
    assert sky.holding(world, target, t0, r, spare="pyroxis") is terra
    offered = sky.flybys(
        world, r, v, t0, target, HOURS, leaving=None, ceiling=CEILING, floor_radii=FLOOR
    )
    bent = min((one for one in offered if one.hours <= CEILING), key=lambda one: one.hours)
    a_max = 0.5 * 5400.0
    route = _route(world, bent, terra, t0, sky.eject_wait(world, target, t0, r, v, bent.v1))
    #: The first minute is still the departure, not a coast toward the pass --
    #: even with no world named as home: the hold itself keeps the departure.
    bare = _route(world, bent, None, t0, sky.eject_wait(world, target, t0, r, v, bent.v1))
    _, leg, want = assist.steer_pass(
        world, target, bare, assist.Leg(), t0, r, v, a_max=a_max, dt=1.0 / 1440
    )
    assert leg.stage == assist.DEPART and want is None
    due = route.arrive + sky.brake_days(world, bent.dv_in, a_max, target)
    flown = _fly(world, target, route, t0, r, v, until=due + 1.0, a_max=a_max)
    assert not flown.struck, f"корпус разбился: {flown.struck}"
    assert flown.captured, "из дрейфа пролёт кончился на круге стоянки"
    assert flown.closest >= route.floor


def test_the_numbers_here_are_the_vaults_own(constants: Constants) -> None:
    """The grid, the ceilings and the floor written out above are the vault's
    (D-317, D-341): the build is read here and nowhere else in this file."""
    assert float(constants[R.ORBIT_SLIDER_FROM_HOURS]) == HOURS[0]
    assert course.flyby_grid(constants) == HOURS
    assert float(constants[R.ORBIT_LONGEST_DAYS]) * HOURS_PER_DAY == CEILING
    assert float(constants[R.ORBIT_FLYBY_LONGEST_DAYS]) * HOURS_PER_DAY == HOURS[-1]
    assert float(constants[R.ORBIT_FLYBY_FLOOR_RADII]) == FLOOR
