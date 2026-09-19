# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The sky flown, not tabled (D-289).

A crossing is an order the helm flies tick by tick under five bodies, and the
tanks pay as the engines burn. What this file pins is what D-289 adds beyond
the order arriving: the hull that runs dry goes adrift and its loss is booked
at the forecast's hour; the loss job asks the arithmetic again before it
kills; a drifter refuelled lays a course from the void; and two ticks on one
hull burn once.
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ship_kit import (
    _fast_sample,
    _flightworthy,
    _flown,
    _fuel,
    _in_orbit,
    _laid,
    _planet,
    _port,
    _shipwright,
)
from src import sky
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import jobs, oxygen, ship
from src.engine.ship import fate, helm, sim
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.job import Job, JobKind, JobState
from src.models.ship import Ship
from src.models.world import Node, Planet

#: What is left in the tank of a hull sent out to run dry: units of fuel.
DROP = 2.0


async def _events(session: AsyncSession, kind: EventKind) -> list[Event]:
    return list((await session.execute(select(Event).where(Event.kind == kind))).scalars().all())


async def _loss_jobs(session: AsyncSession) -> list[Job]:
    return list(
        (
            await session.execute(
                select(Job).where(Job.kind == JobKind.SHIP_LOSS, Job.state == JobState.PENDING)
            )
        )
        .scalars()
        .all()
    )


async def _under_way(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> tuple[Ship, Body, Node, datetime, dict]:
    """A hull in Terra's orbit, under way to Aurora on the fast end of the slider.

    The moment handed back is the one the **arc** begins at, not the one the
    order was given at: an order waits for the ejection window (D-316), and
    how long it waits depends on where the hull stands on its circle. A test
    that ticked an hour from the order was ticking an hour of waiting as often
    as an hour of burning, by the phase of the day it ran on.
    """
    home = await _port(session, name="Космодром столицы")
    await _port(session, name="Космодром Мерида", planet=Planet.AURORA)
    aurora = await _planet(session, Planet.AURORA)
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    await _fuel(session, connector, 5000)
    owner.node_id = connector.id
    await session.flush()
    await _in_orbit(session, constants, catalog, owner, vessel)
    moment = datetime.now(UTC)
    fast = await _fast_sample(session, constants, catalog, vessel, Planet.AURORA)
    await ship.fly(
        session, constants, catalog, owner, vessel, aurora, hours=fast["hours"], now=moment
    )
    #: Named, not ticked: the sky is left exactly where the order left it --
    #: some of these tests are about the first tick there ever is -- and a
    #: caller that asks for an hour of the arc gets the wait swept up in the
    #: same step, which burns nothing (a hull waiting spends no fuel).
    return vessel, owner, aurora, moment + timedelta(hours=fast["wait"]), fast


async def test_a_hull_that_runs_dry_goes_adrift_and_is_fetched_by_fuel(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The tanks pay as the engines burn, and when they cannot, the hull coasts.

    Ordered with a full tank, drained to a few units: the helm burns what
    there is and falls silent. Everybody aboard is told, the console reads a
    drift with a verdict, and the loss is booked exactly when the verdict is
    not "for ever". Refuelled, the same hull takes an order from where it is
    -- adrift is a place one is fetched from (D-289).
    """
    vessel, owner, aurora, moment, _ = await _under_way(session, constants, catalog)
    #: A few units, not a share of the burn: the tank was most of the mass,
    #: and a hull this light buys a whole departure with a share of it.
    aboard = await ship.fuel_aboard(session, constants, catalog, vessel)
    await ship._spend(
        session, await ship.fuel_stacks(session, constants, catalog, vessel), aboard - DROP
    )
    await session.flush()

    last = await _flown(
        session, constants, catalog, vessel, since=moment, until=moment + timedelta(hours=12)
    )
    assert vessel.course is None and vessel.docked_node_id is None, "заказ снят, корпус нигде"
    assert vessel.sky_at is not None and vessel.lost_at is None, "но он в небе и не потерян"
    assert await ship.fuel_aboard(session, constants, catalog, vessel) < 0.5, "баки сухие"
    told = await _events(session, EventKind.SHIP_ADRIFT)
    assert len(told) == 1 and told[0].payload["crew"] == 1
    assert told[0].payload["crew0_identity_id"] == str(owner.identity_id)

    summary = await ship.profile(session, constants, catalog, vessel)
    assert summary["stage"] == "adrift"
    seen = summary["sky"]
    assert seen is not None and len(seen["inertia"]["trace"]) >= 2
    verdict = seen["inertia"]["kind"]
    booked = await _loss_jobs(session)
    assert (verdict != sky.STABLE) == (len(booked) == 1), (
        "гибель забронирована ровно тогда, когда прогноз её видит"
    )
    #: A drifter is offered courses from where it is, and the map draws it.
    assert any(route["planet"] == Planet.AURORA.value for route in summary["routes"])
    drawn = await ship.passages(session)
    assert vessel.node_id in drawn and drawn[vessel.node_id]["to"] is None

    #: Fuel walked aboard by a rescuer -- here, simply put there -- and the
    #: hull is ordered on from the void.
    connector = await session.get(Node, vessel.connector_node_id)
    await _fuel(session, connector, 5000)
    arrives = await ship.fly(
        session, constants, catalog, owner, vessel, aurora, now=last + timedelta(minutes=1)
    )
    assert arrives > last and vessel.course is not None
    assert vessel.course["target"] == aurora.key


async def test_the_loss_job_asks_the_arithmetic_again_before_it_kills(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Booked at the forecast's hour, the job re-verifies (D-289).

    Two drifters on the same line into the star. One is left alone and dies
    with its crew when the hour comes; the other has an order by then -- it
    was refuelled and sent on -- and the job leaves it be.
    """
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        home = await _port(session, name="Космодром столицы")
        await _port(session, name="Космодром Мерида", planet=Planet.AURORA)
        _, owner = await _shipwright(session, home, foundations=2)
        doomed = await _laid(session, constants, owner, home)
        await _flightworthy(session, constants, catalog, doomed)
        connector = await session.get(Node, doomed.connector_node_id)
        await _fuel(session, connector, 5000)
        owner.node_id = connector.id
        await session.flush()
        await _in_orbit(session, constants, catalog, owner, doomed)

        #: Cast into the void by hand: off the circle, five units out from
        #: Terra, falling straight at the star at the planet's own speed.
        world = await sim.system(session, constants)
        terra = world.body(Planet.TERRA.value)
        t = await ship.sky_days(session, now)
        p, vp = sky.place(terra, t)
        here = (float(p[0, 0]) + 5.0, float(p[0, 1]))
        outward = np.array(here) / np.hypot(*here)
        speed = float(np.hypot(*vp[0]))
        falling = tuple(-outward * speed)
        doomed.docked_node_id = None
        sim._write_state(doomed, here, (falling[0], falling[1]), at=now)
        await session.flush()
        verdict = await fate.book_loss(
            session, constants, doomed, world, now=now, t=t, r=here, v=falling
        )
        assert verdict.kind == sky.CRASH and verdict.body == "star"
        booked = await _loss_jobs(session)
        assert len(booked) == 1
        due, doomed_id, owner_id = booked[0].run_at, doomed.id, owner.id

    assert await jobs.run_one(factory, now=due) is not None

    async with factory() as session:
        doomed = await session.get(Ship, doomed_id)
        assert doomed.lost_at == due, "корпус потерян в час прогноза"
        owner = await session.get(Body, owner_id)
        assert owner.died_at is not None, "и экипаж с ним"
        lost = await _events(session, EventKind.SHIP_LOST)
        assert len(lost) == 1 and lost[0].payload["fate"] == sky.CRASH
        assert lost[0].payload["body"] == "star"


async def test_a_drifter_with_an_order_by_the_hour_is_left_alone(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The other half of the re-verification: an order since the booking
    means the forecast is stale, and the job does nothing."""
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        home = await _port(session, name="Космодром столицы")
        await _port(session, name="Космодром Мерида", planet=Planet.AURORA)
        _, owner = await _shipwright(session, home)
        vessel = await _laid(session, constants, owner, home)
        await _flightworthy(session, constants, catalog, vessel)
        connector = await session.get(Node, vessel.connector_node_id)
        await _fuel(session, connector, 5000)
        owner.node_id = connector.id
        await session.flush()
        await _in_orbit(session, constants, catalog, owner, vessel)
        world = await sim.system(session, constants)
        terra = world.body(Planet.TERRA.value)
        t = await ship.sky_days(session, now)
        p, vp = sky.place(terra, t)
        here = (float(p[0, 0]) + 5.0, float(p[0, 1]))
        outward = np.array(here) / np.hypot(*here)
        falling = tuple(-outward * float(np.hypot(*vp[0])))
        vessel.docked_node_id = None
        sim._write_state(vessel, here, (falling[0], falling[1]), at=now)
        await session.flush()
        await fate.book_loss(session, constants, vessel, world, now=now, t=t, r=here, v=falling)
        booked = await _loss_jobs(session)
        due, ship_id = booked[0].run_at, vessel.id
        #: An order in the meantime: the tanks were filled and the hull sent
        #: on. The row carries a course, and that is what the job reads.
        aurora = await _planet(session, Planet.AURORA)
        await ship.fly(
            session, constants, catalog, owner, vessel, aurora, now=now + timedelta(minutes=1)
        )
        assert vessel.course is not None

    assert await jobs.run_one(factory, now=due) is not None

    async with factory() as session:
        vessel = await session.get(Ship, ship_id)
        assert vessel.lost_at is None, "корпус под заказом не теряется по старому прогнозу"
        assert not await _events(session, EventKind.SHIP_LOST)


async def test_a_hull_in_orbit_is_a_body_in_the_sky_and_burns_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """In orbit is no mooring any more (D-354): the climb leaves the hull in
    the sky with a place and a speed, no node under it, on the circle round
    its planet; the tick moves its stamp and burns nothing; the console says
    it is in orbit and draws its lap round the planet."""
    home = await _port(session, name="Космодром столицы")
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    await _fuel(session, connector, 3000)
    owner.node_id = connector.id
    await session.flush()
    await _in_orbit(session, constants, catalog, owner, vessel)
    assert vessel.sky_at is not None and vessel.docked_node_id is None
    assert vessel.berth is None and vessel.course is None

    world = await sim.system(session, constants)
    terra = world.body(Planet.TERRA.value)
    park = sky.park_of(world, terra)
    for hours in (0, 1, 5):
        at = vessel.sky_at + timedelta(hours=hours)
        found = await sim.state_at(session, constants, vessel, now=at)
        assert found is not None
        r, _, t = found
        p, _ = sky.place(terra, t)
        assert np.hypot(r[0] - p[0, 0], r[1] - p[0, 1]) == pytest.approx(park, rel=1e-6)
    before = await ship.fuel_aboard(session, constants, catalog, vessel)
    report = await helm.tick_sky(session, constants, catalog, now=vessel.sky_at + timedelta(days=1))
    assert report.get("flown", 0) == 0
    assert await ship.fuel_aboard(session, constants, catalog, vessel) == before
    summary = await ship.profile(session, constants, catalog, vessel)
    assert summary["stage"] == "orbit" and summary["planet"] == "terra"
    inertia = summary["sky"]["inertia"]
    assert inertia["kind"] == sky.STABLE and inertia["around"] == "terra"
    #: And the air is the hull's own: nothing outside to open the hatch onto.
    assert await oxygen.sealed(session, vessel)


async def test_two_ticks_on_one_hull_burn_once(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The row is locked for the stretch: two workers ticking the same hull
    to the same hour spend one stretch of fuel, not two.

    The bound is physics: an hour at full thrust is all the delta-v an hour can
    burn, and a double burn is twice that.
    """
    async with factory() as session, session.begin():
        vessel, _, _, moment, _ = await _under_way(session, constants, catalog)
        ship_id = vessel.id

    #: Through the ejection window first (D-316), and only then the hour that
    #: is measured. A single step spanning the wait as well carries the whole
    #: departure impulse, which is more delta-v than an hour of thrust -- and
    #: an hour of thrust is the bound below. What the two workers race over
    #: has to be an hour of the arc, not the arc's beginning.
    async with factory() as session, session.begin():
        await helm.tick_sky(session, constants, catalog, now=moment)

    async with factory() as session:
        vessel = await session.get(Ship, ship_id)
        assert vessel is not None and vessel.sky_at == moment
        before = await ship.fuel_aboard(session, constants, catalog, vessel)
        weight = await ship.mass(session, constants, catalog, vessel)
        klass = await ship.engine_class(session, constants, vessel)
        a_max = (await ship.ratio(session, constants, catalog, vessel)) * float(
            constants[R.ORBIT_THRUST_SCALE]
        )

    later = moment + timedelta(hours=1)

    async def tick() -> None:
        async with factory() as session, session.begin():
            await helm.tick_sky(session, constants, catalog, now=later)

    await asyncio.gather(tick(), tick())

    async with factory() as session:
        vessel = await session.get(Ship, ship_id)
        assert vessel.sky_at == later
        after = await ship.fuel_aboard(session, constants, catalog, vessel)
        most = sim.fuel_for_dv(constants, weight, a_max / 24.0, klass)
        assert 0 < before - after <= most * 1.01, "час тяги сожжён один раз, не дважды"


async def test_a_coasting_hull_is_restamped_by_the_tick_and_read_without_writing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The coast is arithmetic from the stamp; the tick moves the stamp along
    once in `orbit.restamp_hours` and counts the coast ahead there. The
    console and the map read what the tick wrote and write nothing.
    """
    vessel, _, _, moment, _ = await _under_way(session, constants, catalog)
    aboard = await ship.fuel_aboard(session, constants, catalog, vessel)
    await ship._spend(
        session, await ship.fuel_stacks(session, constants, catalog, vessel), aboard - DROP
    )
    await session.flush()
    last = await _flown(
        session, constants, catalog, vessel, since=moment, until=moment + timedelta(hours=12)
    )
    assert vessel.course is None and vessel.forecast is not None
    stamped, counted = vessel.sky_at, dict(vessel.forecast)

    #: Under the cadence: the tick leaves the stamp where it is.
    soon = last + timedelta(hours=float(constants[R.ORBIT_RESTAMP_HOURS]) / 2)
    await helm.tick_sky(session, constants, catalog, now=soon)
    await session.refresh(vessel)
    assert vessel.sky_at == stamped and vessel.forecast == counted
    #: Past it: the stamp moves, and the coast ahead is counted afresh.
    later = last + timedelta(hours=float(constants[R.ORBIT_RESTAMP_HOURS]) + 1)
    await helm.tick_sky(session, constants, catalog, now=later)
    await session.refresh(vessel)
    assert vessel.sky_at == later and vessel.forecast["since"] != counted["since"]

    #: A read is a read (the quality bar): the console and the map leave the
    #: row and the journal as they found them.
    row = (vessel.sky_at, vessel.sky_x, vessel.sky_y, dict(vessel.forecast))
    told = len(await _events(session, EventKind.SHIP_ADRIFT))
    summary = await ship.profile(session, constants, catalog, vessel)
    drawn = await ship.passages(session)
    await session.flush()
    await session.refresh(vessel)
    assert (vessel.sky_at, vessel.sky_x, vessel.sky_y, dict(vessel.forecast)) == row
    assert len(await _events(session, EventKind.SHIP_ADRIFT)) == told
    assert summary["sky"]["inertia"]["kind"] == vessel.forecast["kind"]
    assert drawn[vessel.node_id]["arc"] == vessel.forecast["trace"]


async def test_a_hull_under_way_carries_the_coast_ahead_at_the_coaster_cadence(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """ "If the engines fell silent now": the tick counts it for a hull under
    an order too, at the coaster's cadence and not every minute, and never in
    a read."""
    vessel, _, _, moment, _ = await _under_way(session, constants, catalog)
    assert vessel.forecast is None, "заказ прогноза не считает: его пишет тик"
    first = moment + timedelta(hours=1)
    await helm.tick_sky(session, constants, catalog, now=first)
    await session.refresh(vessel)
    assert vessel.forecast is not None and vessel.course is not None
    counted = vessel.forecast["since"]
    await helm.tick_sky(session, constants, catalog, now=first + timedelta(hours=1))
    await session.refresh(vessel)
    assert vessel.forecast["since"] == counted, "час спустя прогноз тот же"
    later = first + timedelta(hours=float(constants[R.ORBIT_RESTAMP_HOURS]) + 1)
    await helm.tick_sky(session, constants, catalog, now=later)
    await session.refresh(vessel)
    assert vessel.forecast["since"] != counted, "за каденцией прогноз пересчитан"
    seen = (await ship.profile(session, constants, catalog, vessel))["sky"]
    assert seen is not None and seen["inertia"]["kind"] == vessel.forecast["kind"]


async def test_a_hull_under_an_order_is_taken_by_the_ground(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The ground is the ground whether a hull coasts onto it or flies into it.

    D-289 wrote the deaths of a drift alone, and the autopilot passed through a
    planet without noticing: the tick moved the hull, nobody asked where it
    had got to, and a point mass flung out whatever came near its centre
    (OQ-120). Now the same reading the coast is flown against answers under an
    order too: the crew die of the same cause, the row carries the same
    verdict, and the tick counts it.
    """
    vessel, owner, aurora, moment, _ = await _under_way(session, constants, catalog)
    world = await sim.system(session, constants)
    terra = world.body(Planet.TERRA.value)
    at = moment + timedelta(minutes=1)
    t = await ship.sky_days(session, at)
    p, vp = sky.place(terra, t)
    #: Put the hull just above Terra by hand and aim it at the middle: under
    #: an order, so the helm is steering, not coasting.
    here = (float(p[0, 0]) + terra.radius * 1.2, float(p[0, 1]))
    falling = (float(vp[0, 0]) - float(np.sqrt(terra.mu / terra.radius)), float(vp[0, 1]))
    sim._write_state(vessel, here, falling, at=at)
    await session.flush()
    assert vessel.course is not None, "приказ на строке, борт под автопилотом"

    #: Long enough for the fall to reach the surface: a fifth of a radius at
    #: the speed of a circle grazing it is a quarter of an hour.
    report = await helm.tick_sky(session, constants, catalog, now=at + timedelta(minutes=20))
    await session.refresh(vessel)
    assert report["struck"] == 1, "тик посчитал корпус, который забрала земля"
    assert vessel.lost_at is not None, "борт потерян"
    assert vessel.course is None, "приказа больше нет"
    assert vessel.forecast["kind"] == sky.CRASH and vessel.forecast["body"] == "terra"
    assert (await session.get(Body, owner.id)).died_at is not None, "экипаж погиб"


async def test_a_hull_under_an_order_is_lost_past_the_edge_of_the_system(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The other end the ground has: `orbit.system_radius`.

    The same reading answers both, and the two ends are not the same death --
    one is a crash on a named body, the other an escape with nothing to name.
    Both take the crew, because the owner asked for both (D-289): carried out
    of the system, or down on a planet or the star, and the notice is a death.
    """
    vessel, owner, aurora, moment, _ = await _under_way(session, constants, catalog)
    world = await sim.system(session, constants)
    at = moment + timedelta(minutes=1)
    #: Just outside the edge already, and running: nothing to fall back to.
    beyond = world.edge * 1.01
    sim._write_state(vessel, (beyond, 0.0), (world.edge, 0.0), at=at)
    await session.flush()
    assert vessel.course is not None, "приказ на строке, борт под автопилотом"

    report = await helm.tick_sky(session, constants, catalog, now=at + timedelta(minutes=2))
    await session.refresh(vessel)
    assert report["struck"] == 1, "тик посчитал корпус, который унесло из системы"
    assert vessel.lost_at is not None and vessel.course is None, "борт потерян, приказа нет"
    assert vessel.forecast["kind"] == sky.ESCAPE, "не падение, а уход"
    assert vessel.forecast["body"] is None, "и называть нечего"
    assert (await session.get(Body, owner.id)).died_at is not None, "экипаж погиб"


async def _coasting_over_terra(
    session: AsyncSession, constants: Constants, catalog: Catalog, *, pyroxis: bool = False
) -> tuple[Ship, sky.System, sky.Body]:
    """A hull off the mooring and coasting where the parking circle put it:
    what every hull at a planet becomes once the orbital node is gone."""
    home = await _port(session, name="Космодром столицы")
    if pyroxis:
        await _planet(session, Planet.PYROXIS)
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    await _fuel(session, connector, 3000)
    owner.node_id = connector.id
    await session.flush()
    await _in_orbit(session, constants, catalog, owner, vessel)
    found = await sim.state_at(session, constants, vessel, now=vessel.sky_at)
    assert found is not None
    vessel.docked_node_id = None
    sim._write_state(vessel, found[0], found[1], at=vessel.sky_at)
    await session.flush()
    world = await sim.system(session, constants)
    return vessel, world, world.body(Planet.TERRA.value)


async def test_a_drifter_on_a_planets_circle_keeps_to_it_through_the_restamps(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hull coasting on Terra's circle with nothing burning stays on it
    (D-354): twelve days of restamps under the whole sky, the forecast
    stable all the way, no loss booked.

    Before the tide (`sky.field.pull`) this hull was a fifth to nine tenths
    off its circle within these days, and in the ground in four to twelve of
    them by where Pyroxis stood, while every forecast along the way said
    "stable": Pyroxis pulled it and not Terra, and the restamps flew exactly
    that. Between two restamps the reading is Kepler's, and it reads what
    the next restamp flies.
    """
    #: Pyroxis in the sky: the world that pulled the hull off Terra's circle.
    #: Without it the test world is Terra alone and there is nothing to pull.
    vessel, world, terra = await _coasting_over_terra(session, constants, catalog, pyroxis=True)
    start = vessel.sky_at
    park = sky.park_of(world, terra)

    async def gap(r: tuple[float, float], at: datetime) -> float:
        p, _ = sky.place(terra, await ship.sky_days(session, at))
        return float(np.hypot(r[0] - p[0, 0], r[1] - p[0, 1]))

    stale = timedelta(hours=float(constants[R.ORBIT_RESTAMP_HOURS]))
    moment = start
    for _ in range(12 * 4):
        read = await sim.state_at(session, constants, vessel, now=moment + stale)
        moment += stale
        await helm.tick_sky(session, constants, catalog, now=moment)
        await session.refresh(vessel)
        assert vessel.sky_at == moment, "дрейфующий корпус не переставлен тиком"
        flown = (float(vessel.sky_x), float(vessel.sky_y))
        assert await gap(flown, moment) == pytest.approx(park, rel=0.01)
        assert read is not None
        assert float(np.hypot(read[0][0] - flown[0], read[0][1] - flown[1])) < 0.01 * park
    assert vessel.lost_at is None and vessel.forecast["kind"] == sky.STABLE
    assert not await _loss_jobs(session)


async def test_a_hull_on_a_close_circle_is_read_by_kepler_between_restamps(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Between two restamps a hull on Terra's circle is placed by Kepler, one
    at a time and in the sighting's batch alike -- no integrator step is
    taken (D-354). A restamp still flies it."""
    vessel, _, _ = await _coasting_over_terra(session, constants, catalog)
    later = vessel.sky_at + timedelta(hours=3)
    flown = await sim.state_at(session, constants, vessel, now=later, exact=True)

    def no_steps(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("прочтено интегратором, а не Кеплером")

    monkeypatch.setattr(sim.sky, "advance", no_steps)
    read = await sim.state_at(session, constants, vessel, now=later)
    table = await sim.states_at(session, constants, [vessel], now=later)
    assert read is not None and flown is not None
    assert read[0] == pytest.approx(table[vessel.id][0])
    #: And what it reads is what the integrator would have flown.
    world = await sim.system(session, constants)
    assert math.hypot(read[0][0] - flown[0][0], read[0][1] - flown[0][1]) < (
        0.01 * world.dock_radius
    )


async def test_a_restamp_that_meets_the_ground_loses_the_hull(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A coast that comes down between two restamps is lost where it came
    down (D-354), not flown through the planet and found on its far side;
    and one whose coast has turned into a fall has its hour booked at the
    restamp that sees it, though the forecast it carried said "stable"."""
    vessel, world, terra = await _coasting_over_terra(session, constants, catalog)
    #: At rest beside Terra a unit and a half out: it falls, and meets the
    #: ground in about ten hours -- after the first restamp, before the second.
    start = vessel.sky_at
    t = await ship.sky_days(session, start)
    p, vp = sky.place(terra, t)
    sim._write_state(
        vessel,
        (float(p[0, 0]) + 1.5, float(p[0, 1])),
        (float(vp[0, 0]), float(vp[0, 1])),
        at=start,
    )
    #: A stale word from before: the coast used to be a stable one.
    vessel.forecast = {"kind": sky.STABLE, "since": start.isoformat()}
    await session.flush()
    stale = timedelta(hours=float(constants[R.ORBIT_RESTAMP_HOURS]))

    await helm.tick_sky(session, constants, catalog, now=start + stale)
    await session.refresh(vessel)
    assert vessel.lost_at is None
    assert vessel.forecast["kind"] == sky.CRASH and vessel.forecast["body"] == "terra"
    assert len(await _loss_jobs(session)) == 1, "падение записано заданием"

    report = await helm.tick_sky(session, constants, catalog, now=start + 2 * stale)
    await session.refresh(vessel)
    assert report["struck"] == 1
    assert vessel.lost_at is not None and start + stale < vessel.lost_at < start + 2 * stale
    lost = await _events(session, EventKind.SHIP_LOST)
    assert len(lost) == 1 and lost[0].payload["body"] == "terra"
