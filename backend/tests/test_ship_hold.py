# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What a held pair does when something goes wrong (D-289, wave 3).

A lost reference takes the held hull with it; two consents in one second
make one edge; a holder locked by another hand is let go by the next tick;
an undocking beside a locked row takes the edge and leaves the mark to the
tick; a holder locked at the loss is lost by the next tick; an order to a
hull gone by the hour is refused; a target ordered away leaves the chaser
adrift.

The meeting and the docking themselves are `test_ship_meet`; the rescue
both files arrange is `ship_kit`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ship_kit import (
    DRIFTER_HEADING,
    FIRST_HEADING,
    RESCUER_HEADING,
    SECOND_HEADING,
    _drifting,
    _events,
    _fuel,
    _hull,
    _joined,
    _met,
    _planet,
    _plunging,
    _port,
)
from src import sky
from src.constants import Catalog, Constants
from src.engine import jobs, ship, travel
from src.engine.ship import fate, helm, sighting, sim
from src.models.event import EventKind
from src.models.identity import Body
from src.models.job import Job, JobKind, JobState
from src.models.ship import Ship
from src.models.world import Node, Planet


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
        aurora_id = (await _planet(session, Planet.AURORA)).id

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
        session, constants, catalog, home, fuel=5000, heading=FIRST_HEADING
    )
    last = await _drifting(session, constants, catalog, doomed, doomed_owner)
    await _plunging(session, constants, doomed, now=last + timedelta(minutes=1))
    assert doomed.forecast["kind"] == sky.CRASH and doomed.forecast["body"] == "terra"
    rescuer, rescuer_owner = await _hull(
        session, constants, catalog, home, fuel=5000, heading=SECOND_HEADING
    )
    since = last + timedelta(minutes=2)
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
    aurora = await _planet(session, Planet.AURORA)
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
