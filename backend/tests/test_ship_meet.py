# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two hulls meeting in the sky (D-289, wave 3).

A drifter in sight may be the target of an order; the helm comes to rest
beside it and the two fly as one; from the hold either commander may ask
to dock, and with both consents the connectors are joined by an edge the
crew walk across; a new order parts the pair; a lost reference takes the
held hull with it; a foreign hull is sighted within the radius and the
journal says so once; two consents in one second make one edge.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ship_kit import (
    _flightworthy,
    _flown,
    _fuel,
    _in_orbit,
    _laid,
    _orbit,
    _port,
    _shipwright,
)
from src import sky
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import jobs, ship, travel
from src.engine.ship import helm, sighting, sim
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.job import Job, JobKind, JobState
from src.models.ship import Ship
from src.models.world import Node, Planet

#: What is left in the tank of a hull sent out to run dry: units of fuel.
DROP = 2.0


async def _events(session: AsyncSession, kind: EventKind) -> list[Event]:
    return list((await session.execute(select(Event).where(Event.kind == kind))).scalars().all())


def _brisk(samples: list[dict]) -> dict:
    """A fast point of the slider with thrust to spare: the geometry to a
    drifter moves between the reading and the order given hours later, and
    the very first `ok` sample has none."""
    fit = [one for one in samples if one["ok"]]
    assert fit, "хоть одна дуга по силам двигателям"
    return fit[min(len(fit) - 1, 3)]


async def _joined(session: AsyncSession, constants: Constants, a: Node, b: Node) -> bool:
    """Whether an edge stands between the two nodes."""
    return any(one.node_id == b.id for one in await travel.exits(session, constants, a))


async def _hull(
    session: AsyncSession, constants: Constants, catalog: Catalog, port: Node, *, fuel: float
) -> tuple[Ship, Body]:
    """A flight-worthy hull of a fresh owner, in Terra's orbit."""
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    await _fuel(session, connector, fuel)
    owner.node_id = connector.id
    await session.flush()
    await _in_orbit(session, constants, catalog, owner, vessel)
    return vessel, owner


async def _drifting(
    session: AsyncSession, constants: Constants, catalog: Catalog, vessel: Ship, owner: Body
) -> datetime:
    """Send the hull to Aurora and let it run dry on the way: adrift near
    Terra, with a forecast on its row. Returns the hour of the last tick."""
    aurora = await _orbit(session, Planet.AURORA)
    moment = datetime.now(UTC)
    forecast = await ship.forecast(session, constants, catalog, vessel, Planet.AURORA, now=moment)
    fast = next(one for one in forecast["samples"] if one["ok"])
    await ship.fly(
        session, constants, catalog, owner, vessel, aurora, hours=fast["hours"], now=moment
    )
    aboard = await ship.fuel_aboard(session, constants, catalog, vessel)
    await ship._spend(
        session, await ship.fuel_stacks(session, constants, catalog, vessel), aboard - DROP
    )
    await session.flush()
    last = await _flown(
        session, constants, catalog, vessel, since=moment, until=moment + timedelta(hours=12)
    )
    assert vessel.course is None and vessel.forecast is not None, "в дрейфе, с прогнозом"
    return last


async def _met(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> tuple[Ship, Body, Ship, Body, datetime]:
    """A drifter and a rescuer of another owner that came to rest beside it."""
    home = await _port(session, name="Космодром столицы")
    await _port(session, name="Космодром Мерида", planet=Planet.AURORA)
    drifter, lost_owner = await _hull(session, constants, catalog, home, fuel=5000)
    last = await _drifting(session, constants, catalog, drifter, lost_owner)
    rescuer, rescuer_owner = await _hull(session, constants, catalog, home, fuel=5000)
    #: A foreign hull is aimed at only in sight: the drifter went adrift a
    #: few units off Terra's circle, and the rescuer sits on it.
    #: Read and ordered at one moment: the slider is a picture of the sky
    #: now, and the order given hours of sky later is another sky.
    since = last + timedelta(minutes=1)
    forecast = await ship.forecast(session, constants, catalog, rescuer, drifter, now=since)
    fast = _brisk(forecast["samples"])
    await ship.fly(
        session, constants, catalog, rescuer_owner, rescuer, drifter, hours=fast["hours"], now=since
    )
    assert rescuer.course is not None and rescuer.course["ship"] == str(drifter.id)
    until = since + timedelta(hours=fast["hours"])
    at = await _flown(
        session, constants, catalog, rescuer, since=since, until=until, slack=timedelta(hours=48)
    )
    assert rescuer.held_ship_id == drifter.id, "рулевой встал рядом и держится"
    return drifter, lost_owner, rescuer, rescuer_owner, at


async def test_a_drifter_in_sight_is_met_and_the_two_fly_as_one(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The order aims at the drifter's forecast; the helm comes to rest beside
    it; from then on the rescuer's place is the drifter's and both owners
    are told."""
    drifter, lost_owner, rescuer, rescuer_owner, at = await _met(session, constants, catalog)
    assert rescuer.held_ship_id == drifter.id, "рулевой встал рядом и держится"
    assert rescuer.course is None and rescuer.docked_node_id is None
    told = await _events(session, EventKind.SHIP_HELD)
    assert {one.actor_identity_id for one in told} == {
        lost_owner.identity_id,
        rescuer_owner.identity_id,
    }
    mine = await sim.state_at(session, constants, rescuer, now=at + timedelta(hours=3))
    theirs = await sim.state_at(session, constants, drifter, now=at + timedelta(hours=3))
    assert mine is not None and theirs is not None
    assert mine[0] == theirs[0] and mine[1] == theirs[1], "летят как один"

    seen = await ship.profile(session, constants, catalog, rescuer)
    assert seen["stage"] == "adrift" and seen["held"] == {
        "ship": str(drifter.id),
        "name": drifter.name,
    }
    assert seen["dock"] == {"asked": False, "wanted": False} and seen["docked_to_ship"] is False
    other = await ship.profile(session, constants, catalog, drifter)
    assert other["held"] == {"ship": str(rescuer.id), "name": rescuer.name}
    #: A held hull is no target any more: a hold has one reference.
    sighted = next(one for one in other["sightings"] if one["ship"] == str(rescuer.id))
    assert sighted["doing"] == "held" and sighted["target"] is False


async def test_docking_takes_both_consents_and_opens_the_hatch(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One consent is a request the other side is told of; the second makes
    the edge, connector to connector. Undocking takes the edge away and
    leaves the hold."""
    drifter, lost_owner, rescuer, rescuer_owner, _ = await _met(session, constants, catalog)
    mine = await session.get(Node, rescuer.connector_node_id)
    theirs = await session.get(Node, drifter.connector_node_id)

    assert not await ship.dock(session, constants, rescuer_owner, rescuer, drifter)
    assert rescuer.dock_ask_ship_id == drifter.id and rescuer.docked_ship_id is None
    asked = await _events(session, EventKind.SHIP_DOCK_ASKED)
    assert len(asked) == 1 and asked[0].actor_identity_id == lost_owner.identity_id
    assert not await _joined(session, constants, mine, theirs)
    seen = await ship.profile(session, constants, catalog, drifter)
    assert seen["dock"] == {"asked": False, "wanted": True}

    assert await ship.dock(session, constants, lost_owner, drifter, rescuer)
    assert rescuer.docked_ship_id == drifter.id and drifter.docked_ship_id == rescuer.id
    assert rescuer.dock_ask_ship_id is None and drifter.dock_ask_ship_id is None
    assert await _joined(session, constants, mine, theirs)
    joined = await _events(session, EventKind.SHIP_DOCKED_SHIP)
    assert {one.actor_identity_id for one in joined} == {
        lost_owner.identity_id,
        rescuer_owner.identity_id,
    }
    seen = await ship.profile(session, constants, catalog, rescuer)
    assert seen["docked_to_ship"] is True and seen["held"]["name"] == drifter.name

    await ship.undock(session, constants, rescuer_owner, rescuer)
    assert rescuer.docked_ship_id is None and drifter.docked_ship_id is None
    assert rescuer.held_ship_id == drifter.id, "расстыковались, но летят как один"
    assert not await _joined(session, constants, mine, theirs)


async def test_docking_is_refused_before_the_hold_and_at_a_pier(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Nothing joins two hulls that are not resting beside each other, and
    hull to hull is space only."""
    home = await _port(session, name="Космодром столицы")
    await _port(session, name="Космодром Мерида", planet=Planet.AURORA)
    one, owner = await _hull(session, constants, catalog, home, fuel=5000)
    two, other = await _hull(session, constants, catalog, home, fuel=5000)
    with pytest.raises(ship.Docked):
        await ship.dock(session, constants, owner, one, two)
    with pytest.raises(ship.TooFar):
        await ship.dock(session, constants, owner, one, one)
    await _drifting(session, constants, catalog, one, owner)
    await _drifting(session, constants, catalog, two, other)
    with pytest.raises(ship.NoPort):
        await ship.dock(session, constants, owner, one, two)
    with pytest.raises(ship.TooFar):
        await ship.fly(session, constants, catalog, owner, one, one)


async def test_a_new_order_parts_the_pair(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The rescuer leaves: the edge comes off, the hold ends, and it coasts
    from the shared state as its own. The drifter refuelled and sent on
    lets the rescuer go the same way."""
    drifter, lost_owner, rescuer, rescuer_owner, at = await _met(session, constants, catalog)
    assert await ship.dock(session, constants, rescuer_owner, rescuer, drifter) is False
    assert await ship.dock(session, constants, lost_owner, drifter, rescuer)
    aurora = await _orbit(session, Planet.AURORA)

    shared = await sim.state_at(session, constants, rescuer, now=at + timedelta(hours=1))
    await ship.fly(
        session, constants, catalog, rescuer_owner, rescuer, aurora, now=at + timedelta(hours=1)
    )
    assert rescuer.held_ship_id is None and rescuer.docked_ship_id is None
    assert drifter.docked_ship_id is None and drifter.held_ship_id is None
    assert shared is not None
    assert (rescuer.sky_x, rescuer.sky_y) == pytest.approx(shared[0]), "ушёл из общей точки"
    parted = await _events(session, EventKind.SHIP_UNDOCKED_SHIP)
    assert {one.actor_identity_id for one in parted} == {
        lost_owner.identity_id,
        rescuer_owner.identity_id,
    }, "о расстыковке сказано обоим"

    #: The other way round: the drifter refuelled and ordered on lets go of
    #: whoever holds on to it.
    fresh, fresh_owner = await _hull(session, constants, catalog, await _port(session), fuel=5000)
    stranded = await _drifting(session, constants, catalog, fresh, fresh_owner)
    helper, helper_owner = await _hull(session, constants, catalog, await _port(session), fuel=5000)
    #: After the drifter's own stamp: an order dated before the target's
    #: last tick would chase where the target will be, not where it is.
    since = stranded + timedelta(minutes=1)
    forecast = await ship.forecast(session, constants, catalog, helper, fresh, now=since)
    fast = _brisk(forecast["samples"])
    await ship.fly(
        session, constants, catalog, helper_owner, helper, fresh, hours=fast["hours"], now=since
    )
    held_at = await _flown(
        session,
        constants,
        catalog,
        helper,
        since=since,
        until=since + timedelta(hours=fast["hours"]),
    )
    assert helper.held_ship_id == fresh.id
    connector = await session.get(Node, fresh.connector_node_id)
    await _fuel(session, connector, 5000)
    await ship.fly(
        session, constants, catalog, fresh_owner, fresh, aurora, now=held_at + timedelta(minutes=1)
    )
    assert helper.held_ship_id is None and helper.forecast is not None
    assert helper.sky_at == held_at + timedelta(minutes=1), "отпущен в момент приказа"
    #: A released holder is a drifter like any other: told, and booked for
    #: the hour its coast ends if it ends.
    released = [
        one
        for one in await _events(session, EventKind.SHIP_ADRIFT)
        if one.payload.get("ship_id") == str(helper.id) and one.payload.get("why") == "released"
    ]
    assert len(released) == 1
    booked = (
        (
            await session.execute(
                select(Job).where(Job.kind == JobKind.SHIP_LOSS, Job.state == JobState.PENDING)
            )
        )
        .scalars()
        .all()
    )
    assert any(one.payload.get("ship") == str(helper.id) for one in booked) == (
        helper.forecast["kind"] != sky.STABLE
    ), "отпущенный забронирован ровно тогда, когда прогноз видит гибель"


async def test_a_foreign_hull_is_sighted_within_the_radius_and_told_once(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The console sees a foreign hull only within the sight radius or at
    the same mooring; the tick tells both owners once when one comes into
    sight, and again only after it has gone out of it."""
    home = await _port(session, name="Космодром столицы")
    await _port(session, name="Космодром Мерида", planet=Planet.AURORA)
    watcher, owner = await _hull(session, constants, catalog, home, fuel=5000)
    stranger, other = await _hull(session, constants, catalog, home, fuel=5000)
    #: Moored at the same orbit: seen, and not a target.
    seen = await ship.profile(session, constants, catalog, watcher)
    found = next(one for one in seen["sightings"] if one["ship"] == str(stranger.id))
    assert found["doing"] == "orbit" and found["mine"] is False and found["target"] is False

    #: Cast into the void by hand, far out: out of sight.
    world = await sim.system(session, constants)
    terra = world.body(Planet.TERRA.value)
    now = datetime.now(UTC)
    t = await ship.sky_days(session, now)
    p, vp = sky.place(terra, t)
    far = (float(p[0, 0]) + 3 * world.sight_radius, float(p[0, 1]))
    stranger.docked_node_id = None
    stranger.park_phase = None
    sim._write_state(stranger, far, (float(vp[0, 0]), float(vp[0, 1])), at=now)
    await session.flush()
    seen = await ship.profile(session, constants, catalog, watcher)
    assert all(one["ship"] != str(stranger.id) for one in seen["sightings"])
    with pytest.raises(ship.TooFar):
        await ship.fly(session, constants, catalog, owner, watcher, stranger, now=now)

    #: Near, and on a circle round Terra so it stays there across the ticks:
    #: seen, and aimed at once the tick has given it a forecast.
    ring = world.sight_radius / 2
    near = (float(p[0, 0]) + ring, float(p[0, 1]))
    around = (float(vp[0, 0]), float(vp[0, 1]) + float(np.sqrt(terra.mu / ring)))
    sim._write_state(stranger, near, around, at=now)
    await session.flush()
    seen = await ship.profile(session, constants, catalog, watcher)
    found = next(one for one in seen["sightings"] if one["ship"] == str(stranger.id))
    assert found["doing"] == "adrift" and found["target"] is False, "без прогноза не цель"
    later = now + timedelta(hours=float(constants[R.ORBIT_RESTAMP_HOURS]) + 1)
    await helm.tick_sky(session, constants, catalog, now=later)
    await session.refresh(stranger)
    assert stranger.forecast is not None
    told = await _events(session, EventKind.SHIP_SIGHTED)
    assert {one.actor_identity_id for one in told} == {owner.identity_id, other.identity_id}
    await helm.tick_sky(
        session,
        constants,
        catalog,
        now=later + timedelta(hours=float(constants[R.ORBIT_RESTAMP_HOURS]) + 1),
    )
    assert len(await _events(session, EventKind.SHIP_SIGHTED)) == len(told), "сказано один раз"
    #: Read at the tick's own hour: the console reads at the clock's, and
    #: the ticks here ran a day ahead of it.
    again = later + timedelta(hours=float(constants[R.ORBIT_RESTAMP_HOURS]) + 1)
    around_then = await sighting.sightings(session, constants, watcher, now=again)
    found = next(one for one in around_then if one["ship"] == str(stranger.id))
    assert found["target"] is True


async def test_a_lost_reference_takes_the_held_hull_with_it(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Two hulls that fly as one fall as one: the loss job that kills the
    reference kills the hull on its hold, crews and all."""
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        drifter, lost_owner, rescuer, rescuer_owner, at = await _met(session, constants, catalog)
        #: The reference cast into the star by hand; the rescuer holds on.
        world = await sim.system(session, constants)
        terra = world.body(Planet.TERRA.value)
        t = await ship.sky_days(session, now)
        p, vp = sky.place(terra, t)
        here = (float(p[0, 0]) + 5.0, float(p[0, 1]))
        outward = np.array(here) / np.hypot(*here)
        falling = tuple(-outward * float(np.hypot(*vp[0])))
        sim._write_state(drifter, here, (falling[0], falling[1]), at=now)
        #: The booking the drift itself made is stale now that the state is
        #: rewritten by hand: closed, so the journal runs this test's own.
        for stale in (
            (
                await session.execute(
                    select(Job).where(Job.kind == JobKind.SHIP_LOSS, Job.state == JobState.PENDING)
                )
            )
            .scalars()
            .all()
        ):
            stale.state = JobState.DONE
            stale.finished_at = now
        await session.flush()
        fate = await helm.book_loss(
            session, constants, drifter, world, now=now, t=t, r=here, v=falling
        )
        assert fate.kind == sky.CRASH
        booked = (
            (
                await session.execute(
                    select(Job).where(Job.kind == JobKind.SHIP_LOSS, Job.state == JobState.PENDING)
                )
            )
            .scalars()
            .all()
        )
        due = booked[0].run_at
        ids = (drifter.id, rescuer.id, lost_owner.id, rescuer_owner.id)

    assert await jobs.run_one(factory, now=due) is not None

    async with factory() as session:
        drifter = await session.get(Ship, ids[0])
        rescuer = await session.get(Ship, ids[1])
        assert drifter.lost_at == due and rescuer.lost_at == due, "погибли оба"
        assert rescuer.held_ship_id is None
        for body_id in ids[2:]:
            body = await session.get(Body, body_id)
            assert body.died_at is not None
        assert len(await _events(session, EventKind.SHIP_LOST)) == 2


async def test_two_consents_in_one_second_make_one_edge(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Both commanders press "dock" at once: the rows are taken in id order,
    so the second consent sees the first and one edge is made, not two."""
    async with factory() as session, session.begin():
        drifter, lost_owner, rescuer, rescuer_owner, _ = await _met(session, constants, catalog)
        ids = (drifter.id, rescuer.id, lost_owner.id, rescuer_owner.id)

    async def consent(ship_id, body_id, other_id) -> bool:
        async with factory() as session, session.begin():
            vessel = await session.get(Ship, ship_id)
            body = await session.get(Body, body_id)
            other = await session.get(Ship, other_id)
            return await ship.dock(session, constants, body, vessel, other)

    joined = await asyncio.gather(consent(ids[0], ids[2], ids[1]), consent(ids[1], ids[3], ids[0]))
    assert sorted(joined) == [False, True], "одно согласие просит, второе стыкует"

    async with factory() as session:
        drifter = await session.get(Ship, ids[0])
        rescuer = await session.get(Ship, ids[1])
        assert drifter.docked_ship_id == rescuer.id and rescuer.docked_ship_id == drifter.id
        mine = await session.get(Node, rescuer.connector_node_id)
        theirs = await session.get(Node, drifter.connector_node_id)
        exits = await travel.exits(session, constants, mine)
        assert sum(one.node_id == theirs.id for one in exits) == 1, "одно ребро, не два"
        assert len(await _events(session, EventKind.SHIP_DOCKED_SHIP)) == 2
