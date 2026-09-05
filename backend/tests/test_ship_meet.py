# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two hulls meeting in the sky (D-289, wave 3).

A drifter in sight may be the target of an order; the helm comes to rest
beside it and the two fly as one; from the hold either commander may ask
to dock, and with both consents the connectors are joined by an edge the
crew walk across; a new order parts the pair; a foreign hull is sighted
within the radius and the journal says so once.

What the pair does afterwards -- under a lock, a loss, or a new order --
is `test_ship_hold`; the rescue both files arrange is `ship_kit`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ship_kit import (
    DRIFTER_HEADING,
    FIRST_HEADING,
    RESCUER_HEADING,
    SECOND_HEADING,
    _drifting,
    _events,
    _flown,
    _fuel,
    _hull,
    _joined,
    _met,
    _orbit,
    _port,
)
from src import sky
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import ship
from src.engine.ship import helm, sighting, sim
from src.models.event import EventKind
from src.models.job import Job, JobKind, JobState
from src.models.world import Node, Planet


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
    one, owner = await _hull(session, constants, catalog, home, fuel=5000, heading=FIRST_HEADING)
    two, other = await _hull(session, constants, catalog, home, fuel=5000, heading=SECOND_HEADING)
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
    watcher, owner = await _hull(
        session, constants, catalog, home, fuel=5000, heading=FIRST_HEADING
    )
    stranger, other = await _hull(
        session, constants, catalog, home, fuel=5000, heading=SECOND_HEADING
    )
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
