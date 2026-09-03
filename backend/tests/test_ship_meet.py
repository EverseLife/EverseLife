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
import math
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
from src.engine.ship import fate, helm, hold, sighting, sim
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.job import Job, JobKind, JobState
from src.models.ship import Ship
from src.models.world import Node, Planet

#: What is left in the tank of a hull sent out to run dry: units of fuel.
DROP = 2.0


async def _events(session: AsyncSession, kind: EventKind) -> list[Event]:
    return list((await session.execute(select(Event).where(Event.kind == kind))).scalars().all())


async def _joined(session: AsyncSession, constants: Constants, a: Node, b: Node) -> bool:
    """Whether an edge stands between the two nodes."""
    return any(one.node_id == b.id for one in await travel.exits(session, constants, a))


#: Where on Terra's circle the hulls of a rescue are put: radians off Terra's
#: own heading at the moment, not an absolute angle -- which way a hull
#: leaves the circle decides whether its dry coast lasts or plunges into
#: Terra, and Terra's heading turns with the year. A drifter sent off from
#: `DRIFTER_HEADING` coasts for weeks; from `PLUNGE_HEADING` it comes down on
#: Terra within the hour; the rescuer sits a little behind the drifter.
DRIFTER_HEADING = 2.5
RESCUER_HEADING = DRIFTER_HEADING + 0.8
PLUNGE_HEADING = -1.75


async def _terra_heading(session: AsyncSession, constants: Constants) -> float:
    world = await sim.system(session, constants)
    terra = world.body(Planet.TERRA.value)
    _, vp = sky.place(terra, await ship.sky_days(session, datetime.now(UTC)))
    return math.atan2(float(vp[0, 1]), float(vp[0, 0]))


async def _hull(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    port: Node,
    *,
    fuel: float,
    heading: float | None = None,
) -> tuple[Ship, Body]:
    """A flight-worthy hull of a fresh owner, in Terra's orbit -- `heading`
    radians off Terra's own heading on the circle, or where its id spins it."""
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    await _fuel(session, connector, fuel)
    owner.node_id = connector.id
    await session.flush()
    await _in_orbit(session, constants, catalog, owner, vessel)
    if heading is not None:
        vessel.park_phase = await _terra_heading(session, constants) + heading
        await session.flush()
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
    drifter, lost_owner = await _hull(
        session, constants, catalog, home, fuel=5000, heading=DRIFTER_HEADING
    )
    last = await _drifting(session, constants, catalog, drifter, lost_owner)
    rescuer, rescuer_owner = await _hull(
        session, constants, catalog, home, fuel=5000, heading=RESCUER_HEADING
    )
    #: A foreign hull is aimed at only in sight: the drifter went adrift a
    #: few units off Terra's circle, and the rescuer sits on it.
    #: One price to a hull, and no slider: the approach profile's own.
    since = last + timedelta(minutes=1)
    forecast = await ship.forecast(session, constants, catalog, rescuer, drifter, now=since)
    (quote,) = forecast["samples"]
    assert quote["ok"] and quote["hours"] > 0 and quote["dv"] > 0
    #: Read at one moment and ordered five minutes later: the quote moves
    #: with the geometry, and the order takes the one of its own moment
    #: rather than looking the console's up and missing it.
    ordered = since + timedelta(minutes=5)
    await ship.fly(
        session,
        constants,
        catalog,
        rescuer_owner,
        rescuer,
        drifter,
        hours=quote["hours"],
        now=ordered,
    )
    assert rescuer.course is not None and rescuer.course["ship"] == str(drifter.id)
    #: The quote of the order's own moment: a hull plunging toward a planet
    #: is a different geometry five minutes on, and the console's number is
    #: not looked up and missed.
    assert rescuer.course["hours"] > 0 and rescuer.course["dv"] > 0
    until = ordered + timedelta(hours=rescuer.course["hours"])
    at = await _flown(
        session, constants, catalog, rescuer, since=ordered, until=until, slack=timedelta(hours=48)
    )
    assert rescuer.held_ship_id == drifter.id, "рулевой встал рядом и держится"
    #: And in SQL, too, nobody of the two is under an order and the pair is
    #: no orphan: a Python None once went into the JSON column as the JSON
    #: value `null`, and every `course IS NOT NULL` took every drifter for
    #: an ordered hull -- the tick locked them all, the sweep filtered none.
    under_orders = (await session.execute(select(Ship.id).where(Ship.course.isnot(None)))).all()
    assert under_orders == [], "JSON null не SQL NULL"
    assert (await session.execute(hold.orphaned_holds())).all() == []
    assert (await session.execute(hold.half_docks())).all() == []
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
    #: The button pressed twice is one request, told of once.
    assert not await ship.dock(session, constants, rescuer_owner, rescuer, drifter)
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
    fresh, fresh_owner = await _hull(
        session, constants, catalog, await _port(session), fuel=5000, heading=DRIFTER_HEADING
    )
    stranded = await _drifting(session, constants, catalog, fresh, fresh_owner)
    helper, helper_owner = await _hull(
        session, constants, catalog, await _port(session), fuel=5000, heading=RESCUER_HEADING
    )
    #: After the drifter's own stamp: an order dated before the target's
    #: last tick would chase where the target will be, not where it is.
    since = stranded + timedelta(minutes=1)
    forecast = await ship.forecast(session, constants, catalog, helper, fresh, now=since)
    (fast,) = forecast["samples"]
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
    #: Gone out of sight: the mover's row forgets the watcher, and the
    #: watcher's row -- which listed the stranger from a move of its own,
    #: and is never rewritten by the tick while it is moored -- is cleaned
    #: by the mover, so that a return is a sighting again.
    watcher.sightings = [str(stranger.id)]
    sim._write_state(stranger, far, (float(vp[0, 0]), float(vp[0, 1])), at=again)
    stranger.forecast = None
    await session.flush()
    away = again + timedelta(hours=float(constants[R.ORBIT_RESTAMP_HOURS]) + 1)
    await helm.tick_sky(session, constants, catalog, now=away)
    await session.refresh(watcher)
    assert stranger.sightings == [] and watcher.sightings == [], "снят с обеих строк"
    p, vp = sky.place(terra, await ship.sky_days(session, away))
    near = (float(p[0, 0]) + ring, float(p[0, 1]))
    around = (float(vp[0, 0]), float(vp[0, 1]) + float(np.sqrt(terra.mu / ring)))
    sim._write_state(stranger, near, around, at=away)
    stranger.forecast = None
    await session.flush()
    back = away + timedelta(hours=float(constants[R.ORBIT_RESTAMP_HOURS]) + 1)
    await helm.tick_sky(session, constants, catalog, now=back)
    assert len(await _events(session, EventKind.SHIP_SIGHTED)) == len(told) + 2, "замечен снова"


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
        verdict = await fate.book_loss(
            session, constants, drifter, world, now=now, t=t, r=here, v=falling
        )
        assert verdict.kind == sky.CRASH
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


async def test_a_holder_locked_by_another_hand_is_let_go_by_the_next_tick(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The reference is ordered on while the holder's row is locked by some
    other command: the release skips it rather than wait, and the next tick
    lets it go from its own state -- a drifter, told and booked."""
    async with factory() as session, session.begin():
        drifter, lost_owner, rescuer, rescuer_owner, at = await _met(session, constants, catalog)
        connector = await session.get(Node, drifter.connector_node_id)
        await _fuel(session, connector, 5000)
        ids = (drifter.id, rescuer.id, lost_owner.id, rescuer_owner.id)
        aurora_id = (await _orbit(session, Planet.AURORA)).id

    holding = asyncio.Event()
    ordered = asyncio.Event()

    async def hand_on_the_holder() -> None:
        async with factory() as session, session.begin():
            await session.get(Ship, ids[1], with_for_update=True)
            holding.set()
            await ordered.wait()

    async def order_the_reference() -> None:
        await holding.wait()
        async with factory() as session, session.begin():
            drifter = await session.get(Ship, ids[0])
            owner = await session.get(Body, ids[2])
            aurora = await session.get(Node, aurora_id)
            await ship.fly(
                session, constants, catalog, owner, drifter, aurora, now=at + timedelta(minutes=1)
            )
        ordered.set()

    #: A regression to a waiting lock would hang here, not fail: bounded.
    await asyncio.wait_for(asyncio.gather(hand_on_the_holder(), order_the_reference()), timeout=60)

    async with factory() as session, session.begin():
        rescuer = await session.get(Ship, ids[1])
        assert rescuer.held_ship_id == ids[0], "отпускание пропустило запертую строку"
        #: A hold the tick has not swept yet is not a hull alongside: no
        #: consent is taken to a reference that has flown off.
        drifter = await session.get(Ship, ids[0])
        owner = await session.get(Body, ids[3])
        with pytest.raises(ship.NoPort):
            await ship.dock(session, constants, owner, rescuer, drifter)
        report = await helm.tick_sky(session, constants, catalog, now=at + timedelta(minutes=2))
        assert report["adrift"] >= 1
        await session.refresh(rescuer)
        assert rescuer.held_ship_id is None and rescuer.course is None
        assert rescuer.forecast is not None and rescuer.sky_at == at + timedelta(minutes=2)
        told = [
            one
            for one in await _events(session, EventKind.SHIP_ADRIFT)
            if one.payload.get("ship_id") == str(ids[1]) and one.payload.get("why") == "released"
        ]
        assert len(told) == 1, "тик отпустил и сказал"


async def test_an_undocking_beside_a_locked_row_takes_the_edge_and_leaves_the_mark_to_the_tick(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The other hull's row is under somebody's hand as this one undocks: the
    edge goes now and so does this row's mark, the other's is not waited for
    (each side holding its own row and wanting the other's is the deadlock).
    The console reads a docking off both rows, so nobody sees the half mark;
    the tick clears it."""
    async with factory() as session, session.begin():
        drifter, lost_owner, rescuer, rescuer_owner, at = await _met(session, constants, catalog)
        assert not await ship.dock(session, constants, rescuer_owner, rescuer, drifter)
        assert await ship.dock(session, constants, lost_owner, drifter, rescuer)
        ids = (drifter.id, rescuer.id, rescuer_owner.id)
        nodes = (rescuer.connector_node_id, drifter.connector_node_id)
        owners = {lost_owner.identity_id, rescuer_owner.identity_id}

    holding = asyncio.Event()
    parted = asyncio.Event()

    async def hand_on_the_drifter() -> None:
        async with factory() as session, session.begin():
            await session.get(Ship, ids[0], with_for_update=True)
            holding.set()
            await parted.wait()

    async def undock_the_rescuer() -> None:
        await holding.wait()
        async with factory() as session, session.begin():
            rescuer = await session.get(Ship, ids[1])
            owner = await session.get(Body, ids[2])
            await ship.undock(session, constants, owner, rescuer)
        parted.set()

    await asyncio.wait_for(asyncio.gather(hand_on_the_drifter(), undock_the_rescuer()), timeout=60)

    async with factory() as session, session.begin():
        drifter = await session.get(Ship, ids[0])
        rescuer = await session.get(Ship, ids[1])
        assert rescuer.docked_ship_id is None and drifter.docked_ship_id == ids[1], (
            "чужая строка не ждалась"
        )
        mine = await session.get(Node, nodes[0])
        theirs = await session.get(Node, nodes[1])
        assert not await _joined(session, constants, mine, theirs), "ребро снято"
        assert (await sighting.ties(session, drifter))["docked_to_ship"] is False, (
            "стыковка читается с обеих строк"
        )
        told = await _events(session, EventKind.SHIP_UNDOCKED_SHIP)
        assert {one.actor_identity_id for one in told} == owners
        await helm.tick_sky(session, constants, catalog, now=at + timedelta(minutes=2))
        await session.refresh(drifter)
        assert drifter.docked_ship_id is None, "тик снял половинку"
        assert rescuer.held_ship_id == ids[0], "удержание осталось"


async def test_a_holder_locked_at_the_loss_is_lost_by_the_next_tick(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The loss job finds the holder's row under somebody's hand and skips it;
    the sweep, next minute, loses it by the verdict on the reference's row --
    not "released" into a coast it never had."""
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        drifter, lost_owner, rescuer, rescuer_owner, at = await _met(session, constants, catalog)
        #: Docked, too: the gangway must go with the loss, whichever row the
        #: loss could take.
        assert not await ship.dock(session, constants, rescuer_owner, rescuer, drifter)
        assert await ship.dock(session, constants, lost_owner, drifter, rescuer)
        nodes = (rescuer.connector_node_id, drifter.connector_node_id)
        world = await sim.system(session, constants)
        terra = world.body(Planet.TERRA.value)
        t = await ship.sky_days(session, now)
        p, vp = sky.place(terra, t)
        here = (float(p[0, 0]) + 5.0, float(p[0, 1]))
        outward = np.array(here) / np.hypot(*here)
        falling = tuple(-outward * float(np.hypot(*vp[0])))
        sim._write_state(drifter, here, (falling[0], falling[1]), at=now)
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
        verdict = await fate.book_loss(
            session, constants, drifter, world, now=now, t=t, r=here, v=falling
        )
        assert verdict.kind == sky.CRASH
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

    holding = asyncio.Event()
    lost = asyncio.Event()

    async def hand_on_the_rescuer() -> None:
        async with factory() as session, session.begin():
            await session.get(Ship, ids[1], with_for_update=True)
            holding.set()
            await lost.wait()

    async def run_the_loss() -> None:
        await holding.wait()
        assert await jobs.run_one(factory, now=due) is not None
        lost.set()

    await asyncio.wait_for(asyncio.gather(hand_on_the_rescuer(), run_the_loss()), timeout=60)

    async with factory() as session, session.begin():
        drifter = await session.get(Ship, ids[0])
        rescuer = await session.get(Ship, ids[1])
        assert drifter.lost_at == due and drifter.forecast["kind"] == sky.CRASH
        assert rescuer.lost_at is None, "гибель пропустила запертую строку"
        assert rescuer.held_ship_id == ids[0]
        mine = await session.get(Node, nodes[0])
        theirs = await session.get(Node, nodes[1])
        assert not await _joined(session, constants, mine, theirs), "трап ушёл с гибелью"
        later = due + timedelta(minutes=1)
        await helm.tick_sky(session, constants, catalog, now=later)
        await session.refresh(rescuer)
        assert rescuer.lost_at == later and rescuer.held_ship_id is None, "тик погубил"
        gone = await _events(session, EventKind.SHIP_LOST)
        assert {one.payload.get("ship_id") for one in gone} == {str(ids[0]), str(ids[1])}
        assert all(one.payload.get("fate") == sky.CRASH for one in gone), "по вердикту опоры"
        released = [
            one
            for one in await _events(session, EventKind.SHIP_ADRIFT)
            if one.payload.get("ship_id") == str(ids[1]) and one.payload.get("why") == "released"
        ]
        assert not released, "не отпущен, а погиб"
        for body_id in ids[2:]:
            assert (await session.get(Body, body_id)).died_at is not None


async def test_an_order_to_a_hull_gone_by_the_hour_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A drifter coming down on Terra within the hour is in sight and adrift,
    and still no target: its line ends before the approach profile gets
    there. The console offers nothing, and the order is refused."""
    home = await _port(session, name="Космодром столицы")
    await _port(session, name="Космодром Мерида", planet=Planet.AURORA)
    doomed, doomed_owner = await _hull(
        session, constants, catalog, home, fuel=5000, heading=PLUNGE_HEADING
    )
    last = await _drifting(session, constants, catalog, doomed, doomed_owner)
    assert doomed.forecast["kind"] == sky.CRASH and doomed.forecast["body"] == "terra"
    rescuer, rescuer_owner = await _hull(
        session, constants, catalog, home, fuel=5000, heading=PLUNGE_HEADING + 0.8
    )
    since = last + timedelta(minutes=1)
    seen = await ship.forecast(session, constants, catalog, rescuer, doomed, now=since)
    assert seen["samples"] == [], "рубка не предлагает пути к тому, кого не будет"
    with pytest.raises(ship.NoArc) as refused:
        await ship.fly(session, constants, catalog, rescuer_owner, rescuer, doomed, now=since)
    assert "ship-target-gone-by-then" in str(refused.value)
    assert rescuer.course is None


async def test_a_target_ordered_away_leaves_the_chaser_adrift(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The drifter is refuelled and ordered on while a rescuer is on its way:
    the target is a hull under an order now, no hull to meet, and the
    chaser's helm lets it coast from where it is, its owner told why."""
    home = await _port(session, name="Космодром столицы")
    await _port(session, name="Космодром Мерида", planet=Planet.AURORA)
    drifter, lost_owner = await _hull(
        session, constants, catalog, home, fuel=5000, heading=DRIFTER_HEADING
    )
    last = await _drifting(session, constants, catalog, drifter, lost_owner)
    rescuer, rescuer_owner = await _hull(
        session, constants, catalog, home, fuel=5000, heading=RESCUER_HEADING
    )
    since = last + timedelta(minutes=1)
    await ship.fly(session, constants, catalog, rescuer_owner, rescuer, drifter, now=since)
    assert rescuer.course is not None and rescuer.course["ship"] == str(drifter.id)
    await _fuel(session, await session.get(Node, drifter.connector_node_id), 5000)
    aurora = await _orbit(session, Planet.AURORA)
    await ship.fly(
        session, constants, catalog, lost_owner, drifter, aurora, now=since + timedelta(minutes=1)
    )
    assert drifter.course is not None
    await helm.tick_sky(session, constants, catalog, now=since + timedelta(minutes=2))
    await session.refresh(rescuer)
    assert rescuer.course is None and rescuer.forecast is not None, "цель ушла -- дрейф"
    told = [
        one
        for one in await _events(session, EventKind.SHIP_ADRIFT)
        if one.payload.get("ship_id") == str(rescuer.id)
    ]
    assert [one.payload.get("why") for one in told] == ["target"]
