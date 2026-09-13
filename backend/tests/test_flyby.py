# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The flyby (D-341): the arithmetic alone, no rows.

Pinned is what the feature stands on:

* the batched Lambert is `astro.lambert`'s own answer, and the integrator
  flies back to where it started when run backwards;
* where the sky lends its pull the slider offers a flyby cheaper than the
  direct arc of the same hour, and where it does not, it offers none;
* no pass is laid under the floor;
* the slider offers one hull its real choices only -- what its engines
  deliver, each cheaper than every faster one -- and of them the largest group
  parted at `orbit.route_gap`, which cuts a far slow group and a fast group
  behind a hole alike;
* past the direct slider the search walks outward a window at a time and
  stops at the first window that adds no choice;
* every hull lays its plan from its own place on its circle;
* the helm flies a flyby through all five bodies, minute by minute, onto the
  target's circle within the promise and never under the floor.
"""

from __future__ import annotations

import inspect
import itertools
import math
from typing import NamedTuple

import numpy as np
import pytest

from sky_kit import system
from src import astro, sky
from src.constants import Constants
from src.constants import registry as R
from src.engine.ship import course
from src.sky import assist, choice, field, flyby, lambert, plan, shoot
from src.units import HOURS_PER_DAY, MINUTES_PER_HOUR, TRACE_POINTS

#: The vault's numbers these tests fly by, written out for the reason
#: `sky_kit` is -- the arithmetic against the vault's shape, not its build --
#: and checked against the build by the last test: the direct arc's twelve
#: days (`orbit.longest_days`, D-317), the slider's grid (`orbit.slider_from_hours`
#: 2, `orbit.slider_step` 10%) on out to the search's own guard, the gap the
#: choices part at (`orbit.route_gap`), the floor, and what the reference hull
#: (thrust 0.5) and the weakest legal one (0.15) deliver in a day of flight
#: (`orbit.thrust_scale` 5400, `orbit.burn_share` 0.5).
LONGEST = 12 * HOURS_PER_DAY
HOURS = course._grid(2.0, 1.1, LONGEST) + tuple(
    one
    for one in course._grid(2.0, 1.1, sky.search_days(system()) * HOURS_PER_DAY)[:-1]
    if one > LONGEST
)
GAP = 2.0
FLOOR = 2.0
REFERENCE = 0.5 * 5400.0 * 0.5
WEAKEST = 0.15 * 5400.0 * 0.5
#: A departure the survey of 2026-09-13 found bent round Pyroxis at the fast
#: end: Terra to Aquatica on sky day 126, a little over three days.
SWING = ("terra", "aquatica", 126.0, 74.81)
#: Where on the parking circle the hulls here sit, radians round the world.
PHASE = 1.0


def _moored(
    world: sky.System, origin: str, t0: float, phase: float = PHASE
) -> tuple[tuple[float, float], tuple[float, float]]:
    """A hull on the parking circle of `origin`, as a place and a velocity."""
    r, v = sky.parking(world, world.body(origin), t0, phase)
    return (float(r[0, 0]), float(r[0, 1])), (float(v[0, 0]), float(v[0, 1]))


def _offers(
    world: sky.System,
    origin: str,
    goal: str,
    t0: float,
    *,
    floor: float = FLOOR,
    phase: float = PHASE,
    reach: float = REFERENCE,
) -> list[sky.Sample]:
    r0, v0 = _moored(world, origin, t0, phase)
    return sky.routes(
        world,
        r0,
        v0,
        t0,
        world.body(goal),
        HOURS,
        leaving=world.body(origin),
        longest=LONGEST,
        reach=reach,
        gap=GAP,
        floor_radii=floor,
    )


def _direct(
    world: sky.System, origin: str, goal: str, t0: float, *, phase: float = PHASE
) -> dict[float, sky.Sample]:
    r0, v0 = _moored(world, origin, t0, phase)
    return {
        one.hours: one
        for one in sky.preview(
            world,
            None,
            r0,
            v0,
            t0,
            world.body(goal),
            tuple(one for one in HOURS if one <= LONGEST),
            leaving=world.body(origin),
        )
    }


def _price(hours: float, dv: float) -> sky.Sample:
    """A point of a slider that is nothing but its hours and its price."""
    return sky.Sample(hours=hours, dv_out=dv, dv_in=0.0, dv=dv, trace=(), revs=0)


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
    and the line drawn for it starts at the hull and bends where the pass is."""
    origin, goal, t0, hours = SWING
    world = system()
    offered = {one.hours: one for one in _offers(world, origin, goal, t0)}
    direct = _direct(world, origin, goal, t0)
    assert hours in offered, "у Пироксиса в этот день есть пролёт"
    bent = offered[hours]
    assert bent.via is not None and bent.via.via == "pyroxis"
    assert bent.dv < direct[hours].dv, "пролёт дешевле прямой дуги того же часа"
    assert bent.dv == pytest.approx(bent.dv_out + bent.via.cost + bent.dv_in)
    #: Every flyby offered within the direct slider beats its hour's direct arc.
    for one in offered.values():
        if one.via is not None and one.hours in direct:
            assert one.dv < direct[one.hours].dv
    #: The kink: the line passes through the place of Pyroxis at the pass, and
    #: it still starts where the hull is and ends at Aquatica.
    assert len(bent.trace) == TRACE_POINTS
    corner = sky.place(world.body("pyroxis"), t0 + bent.via.at)[0][0]
    assert min(math.dist(point, corner) for point in bent.trace) < 1e-6
    assert bent.trace[0] == pytest.approx(_moored(world, origin, t0)[0])
    goal_at = sky.place(world.body(goal), t0 + hours / HOURS_PER_DAY)[0][0]
    assert math.dist(bent.trace[-1], goal_at) < 1e-3


def test_no_flyby_is_offered_where_it_does_not_pay() -> None:
    """Pyroxis to Terra never gains by a pass (the survey: 0% of hours), and
    Terra to Pyroxis gains nothing within the direct slider."""
    world = system()
    assert all(one.via is None for one in _offers(world, "pyroxis", "terra", 3.0))
    assert all(one.hours > LONGEST for one in _offers(world, "terra", "pyroxis", 3.0) if one.via)


def test_no_pass_is_laid_under_the_floor() -> None:
    """Every pass keeps above `orbit.flyby_floor_radii` of the world it goes
    round -- raise the floor and the passes that needed it go or climb -- and
    the conics price a turn sharper than the floor gives as no flyby at all."""
    origin, goal, t0, _ = SWING
    world = system()
    pyroxis = world.body("pyroxis")
    low = [one for one in _offers(world, origin, goal, t0) if one.via is not None]
    high = [one for one in _offers(world, origin, goal, t0, floor=3.0) if one.via is not None]
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


def test_only_real_choices_are_offered() -> None:
    """What the engines cannot deliver is not offered, an hour priced twice is
    its cheaper passage, and a passage slower and no cheaper than a faster one
    is no choice (D-341) -- while one the engines cannot deliver dominates
    nothing."""
    samples = [
        _price(10.0, 100.0),
        _price(20.0, 120.0),
        _price(20.0, 80.0),
        _price(30.0, 80.0),
        _price(40.0, 70.0),
    ]
    offered = choice.choices(samples, reach=math.inf, gap=GAP)
    assert [(one.hours, one.dv) for one in offered] == [(10.0, 100.0), (20.0, 80.0), (40.0, 70.0)]
    #: Engines that give 200 a day give 83 in ten hours: the fast passage is
    #: out of their reach, and the dearer twenty-hour one it outpriced is back.
    weak = choice.choices([_price(10.0, 100.0), _price(20.0, 150.0)], reach=200.0, gap=GAP)
    assert [(one.hours, one.dv) for one in weak] == [(20.0, 150.0)]
    #: On the sky itself: every point deliverable, and the price falls along
    #: the slider -- for the reference hull and for the weakest legal one.
    world = system()
    for reach in (REFERENCE, WEAKEST):
        offered = _offers(world, "terra", "aurora", 101.1, reach=reach)
        assert offered
        assert all(choice.deliverable(one, reach) for one in offered)
        assert all(a.hours < b.hours and a.dv > b.dv for a, b in itertools.pairwise(offered))


def test_the_largest_group_is_offered_at_both_ends() -> None:
    """The choices part wherever the next is more than `orbit.route_gap` times
    longer than the one before, and the slider offers the largest group -- a
    fast group behind a hole in the hours goes, and so does a far slow one."""
    #: The owner's illustration (D-341): the pair at ten and twenty hours is
    #: fuel paid for nothing -- a hundred is five times twenty -- while 950 is
    #: only 1.9 times 500 and stays with the rest.
    hours = (10.0, 20.0, 100.0, 200.0, 400.0, 500.0, 950.0, 1000.0)
    owner = [_price(one, 1000.0 - k) for k, one in enumerate(hours)]
    offered = choice.choices(owner, reach=math.inf, gap=GAP)
    assert [one.hours for one in offered] == [100.0, 200.0, 400.0, 500.0, 950.0, 1000.0]
    #: A far slow group, very long against the rest.
    far = [_price(one, 100.0 - k) for k, one in enumerate((10.0, 15.0, 20.0, 30.0, 100.0))]
    kept = choice.choices(far, reach=math.inf, gap=GAP)
    assert [one.hours for one in kept] == [10.0, 15.0, 20.0, 30.0]
    #: Exactly twice as long is still one group; of two equal groups, the faster.
    pair = [_price(10.0, 50.0), _price(20.0, 40.0)]
    assert len(choice.choices(pair, reach=math.inf, gap=GAP)) == 2
    tie = [_price(10.0, 50.0), _price(15.0, 40.0), _price(100.0, 30.0), _price(150.0, 20.0)]
    assert [one.hours for one in choice.choices(tie, reach=math.inf, gap=GAP)] == [10.0, 15.0]
    #: On the sky: Aquatica to Terra on day 126.4 has direct arcs under a day,
    #: a hole where every arc cuts the corona, and the slider from three days
    #: on -- the fast group behind the hole is not offered.
    world = system()
    offered = _offers(world, "aquatica", "terra", 126.4)
    fastest = choice.front(_direct(world, "aquatica", "terra", 126.4).values(), reach=REFERENCE)
    assert fastest[0].hours * GAP < offered[0].hours, "быстрая группа за дырой в часах"
    assert offered[0].hours > 2 * HOURS_PER_DAY


def test_the_search_walks_outward_and_stops_at_the_first_empty_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the direct slider the flybys are searched a window at a time, each
    reaching `orbit.route_gap` times the slowest choice found so far, until a
    window adds none -- and nothing past it is searched at all (D-341)."""
    world = system()
    bind = inspect.signature(plan.search).bind
    walked = {}
    for origin, goal, t0 in (("terra", "aurora", 101.1), ("terra", "pyroxis", 75.8)):
        windows: list[tuple[float, ...]] = []
        chains: list[list[sky.Sample]] = []
        search, front = plan.search, plan.front

        def searching(*args, _seen=windows, _search=search, **kwargs):  # type: ignore[no-untyped-def]
            _seen.append(bind(*args, **kwargs).arguments["hours"])
            return _search(*args, **kwargs)

        def chaining(*args, _seen=chains, _front=front, **kwargs):  # type: ignore[no-untyped-def]
            chain = _front(*args, **kwargs)
            _seen.append(chain)
            return chain

        monkeypatch.setattr(plan, "search", searching)
        monkeypatch.setattr(plan, "front", chaining)
        offered = _offers(world, origin, goal, t0)
        monkeypatch.undo()
        #: The chain after every window, as the search itself read it.
        assert len(chains) == len(windows)
        assert windows[0] == tuple(one for one in HOURS if one <= LONGEST)
        for k in range(1, len(windows)):
            end = chains[k - 1][-1].hours
            assert windows[k] == tuple(
                one for one in HOURS if windows[k - 1][-1] < one <= GAP * end
            ), "окно доходит до разрыва от конца цепочки"
        added = [
            any(windows[k - 1][-1] < one.hours for one in chains[k]) for k in range(1, len(windows))
        ]
        assert added and not added[-1], "поиск кончился на окне без выбора"
        assert all(added[:-1]), "каждое окно до последнего добавило выбор"
        assert max(one.hours for one in offered) <= windows[-1][-1]
        walked[goal] = (len(windows), chains[-1][-1].hours)
    #: Terra to Aurora on day 101.1 walks on past the direct slider through a
    #: chain of flybys; Terra to Pyroxis on day 75.8 ends at eight days, and
    #: its one window past twelve finds nothing.
    assert walked["aurora"][0] > 2 and walked["aurora"][1] > 30 * HOURS_PER_DAY
    assert walked["pyroxis"][0] == 2 and walked["pyroxis"][1] < LONGEST


def test_a_candidate_is_refined_while_its_conic_price_could_still_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The conics are no bound on what the sky confirms (D-341): a pass whose
    conic price is above what it must beat is refined while the price less
    the measured spread still beats it, and another world's pass at an hour
    already confirmed is refined too when it might come out cheaper -- and
    taken when it does."""
    world = system()
    hour = 100.0
    certain = [_price(50.0, 100.0)]

    def candidate(via: str, dv: float) -> flyby.Candidate:
        return flyby.Candidate(
            hours=hour,
            via=via,
            tau=1.0,
            rp=1.0,
            dv_out=dv,
            dv_pass=0.0,
            dv_in=0.0,
            dv=dv,
            v_in=(1.0, 0.0),
            v_out=(0.0, 1.0),
        )

    #: What the sky makes of each: a pass named 120 by the conics that the sky
    #: confirms at 90, and another named 125 that comes out at 80.
    confirmed = {"pyroxis": 90.0, "terra": 80.0}
    asked: list[list[str]] = []

    def sky_says(*args, **kwargs):  # type: ignore[no-untyped-def]
        batch = args[5]
        asked.append([one.via for one in batch])
        return [
            shoot.Shot(
                candidate=one,
                at=1.0,
                rp=1.0,
                speed_in=1.0,
                speed_out=1.0,
                v1=(0.0, 0.0),
                aim=(0.0, 0.0),
                dv_out=confirmed[one.via],
                dv_pass=0.0,
                dv_in=0.0,
                dv=confirmed[one.via],
            )
            for one in batch
        ]

    monkeypatch.setattr(plan, "refine", sky_says)
    queue = {hour: [candidate("pyroxis", 120.0), candidate("terra", 125.0)]}
    shots = plan._refine_all(
        world,
        (0.0, 0.0),
        (0.0, 0.0),
        0.0,
        world.body("aurora"),
        queue,
        certain,
        reach=math.inf,
        leaving=None,
        floor_radii=FLOOR,
    )
    assert asked == [["pyroxis"], ["terra"]], "обе цены коник выше планки, обе уточнены"
    assert [shot.dv for shot in shots] == [80.0], "у часа — пролёт дешевле"
    #: A pass whose conic price is out of reach even with the spread is not
    #: refined at all.
    asked.clear()
    far = {hour: [candidate("pyroxis", 100.0 / plan._CONIC_SPREAD + 1.0)]}
    none = plan._refine_all(
        world,
        (0.0, 0.0),
        (0.0, 0.0),
        0.0,
        world.body("aurora"),
        far,
        certain,
        reach=math.inf,
        leaving=None,
        floor_radii=FLOOR,
    )
    assert none == [] and asked == []


def test_the_waits_of_a_remembered_slider_are_the_hulls_own() -> None:
    """A slider found again in memory has its waits counted anew for the hull
    reading it, all at once (`sky.eject_waits`) -- and each is exactly the wait
    `sky.eject_wait` gives that departure alone (D-316, D-341)."""
    world = system()
    origin, goal, t0, _ = SWING
    target = world.body(goal)
    rng = np.random.default_rng(11)
    for phase in (0.2, 2.5, 4.9):
        r0, v0 = _moored(world, origin, t0, phase)
        _, vp = sky.place(world.body(origin), t0)
        #: Random departures, and one straight along the way the hull goes
        #: round its world: in the window already, with nothing to wait for.
        along = np.asarray(v0) - vp[0]
        ahead = vp[0] + 30.0 * along / np.hypot(*along)
        wanted = np.vstack([vp[0] + rng.normal(0.0, 40.0, size=(24, 2)), ahead])
        waits = sky.eject_waits(world, target, t0, r0, v0, wanted)
        for row, wait in zip(wanted, waits, strict=True):
            one = sky.eject_wait(world, target, t0, r0, v0, (float(row[0]), float(row[1])))
            assert wait == pytest.approx(one, abs=1e-12)
        assert np.any(waits > 0.0) and waits[-1] == 0.0


def test_each_hull_lays_its_own_plan() -> None:
    """Two hulls on one circle, half a turn apart, at one moment: each flyby
    plan is laid from the hull's own place and velocity -- its line starts at
    the hull, its departure and its wait are its own (D-341)."""
    origin, goal, t0, hours = SWING
    world = system()
    target = world.body(goal)
    plans = []
    for phase in (PHASE, PHASE + math.pi):
        r0, v0 = _moored(world, origin, t0, phase)
        offered = _offers(world, origin, goal, t0, phase=phase)
        bent = next(one for one in offered if one.hours == hours)
        assert bent.via is not None
        assert bent.trace[0] == pytest.approx(r0), "линия начинается у корпуса, а не в центре мира"
        assert bent.wait == pytest.approx(
            sky.eject_wait(world, target, t0, r0, v0, bent.v1) * HOURS_PER_DAY
        )
        plans.append(bent)
    one, other = plans
    assert math.dist(one.trace[0], other.trace[0]) == pytest.approx(
        2 * sky.park_of(world, world.body(origin))
    )
    assert math.dist(one.v1, other.v1) > 1e-3, "уход у каждого свой"
    assert abs(one.wait - other.wait) > 0.1, "окно ухода у каждого своё"


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
    r, v = _moored(world, origin, t0, phase)
    #: The plan is this hull's own, laid from where it sits (D-341), and so is
    #: the wait in it.
    offered = _offers(world, origin, goal, t0, phase=phase)
    bent = next(one for one in offered if one.hours == hours)
    assert bent.via is not None
    a_max = 0.5 * 5400.0
    route = _route(world, bent, home, t0, bent.wait / HOURS_PER_DAY)
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
    offered = sky.routes(
        world,
        r,
        v,
        t0,
        target,
        HOURS,
        leaving=None,
        longest=LONGEST,
        reach=REFERENCE,
        gap=GAP,
        floor_radii=FLOOR,
    )
    bent = min(
        (one for one in offered if one.hours <= LONGEST and one.via is not None),
        key=lambda one: one.hours,
    )
    a_max = 0.5 * 5400.0
    route = _route(world, bent, terra, t0, bent.wait / HOURS_PER_DAY)
    #: The first minute is still the departure, not a coast toward the pass --
    #: even with no world named as home: the hold itself keeps the departure.
    bare = _route(world, bent, None, t0, bent.wait / HOURS_PER_DAY)
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
    """The grid, the direct arc's horizon, the gap, the floor and the engines'
    reach written out above are the vault's (D-317, D-341): the build is read
    here and nowhere else in this file."""
    world = system()
    assert float(constants[R.ORBIT_SLIDER_FROM_HOURS]) == HOURS[0]
    assert course.flyby_grid(constants, sky.search_days(world)) == HOURS
    assert float(constants[R.ORBIT_LONGEST_DAYS]) * HOURS_PER_DAY == LONGEST
    assert float(constants[R.ORBIT_ROUTE_GAP]) == GAP
    assert float(constants[R.ORBIT_FLYBY_FLOOR_RADII]) == FLOOR
    assert course.reach(constants, 0.5) == pytest.approx(REFERENCE)
    assert course.reach(constants, float(constants[R.SHIP_MIN_THRUST_RATIO])) == pytest.approx(
        WEAKEST
    )
