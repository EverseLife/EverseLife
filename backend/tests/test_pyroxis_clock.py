# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The planet's clock: the warn/erupt chain and the free signal (D-197, P6).

The weather queues itself and survives deploys, dead chains and seconds
already taken; the warning outlives the second it was said in, reaches
whoever stands in the field, and the digest tells what the place lived
through. What the eruption then does lives in `test_pyroxis_eruption.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pyroxis_kit import _dweller, _surface
from src.constants import Constants
from src.constants import registry as R
from src.engine import plates
from src.models.event import Event, EventKind
from src.models.job import Job, JobKind, JobState


async def test_the_planets_clock_queues_itself(session: AsyncSession, constants: Constants) -> None:
    """An eruption is the planet's weather, not an event of the server (D-197):
    the world puts its own next one in the journal."""
    await plates.ensure_scheduled(session, now=datetime.now(UTC))
    queued = await session.scalar(select(Job).where(Job.kind == JobKind.PLATES_WARN.value).limit(1))
    assert queued is not None
    period = constants[R.PYROXIS_ERUPTION_PERIOD]
    ahead = (queued.run_at - datetime.now(UTC)).total_seconds() / 3600 / 24
    assert period.min - 1 <= ahead <= period.max + 1

    #: Asked twice, queued once: the clock must neither be lost nor doubled.
    await plates.ensure_scheduled(session, now=datetime.now(UTC))
    both = (
        (await session.execute(select(Job).where(Job.kind == JobKind.PLATES_WARN.value)))
        .scalars()
        .all()
    )
    assert len(both) == 1


async def test_two_processes_of_one_deploy_start_one_clock(
    session: AsyncSession, constants: Constants
) -> None:
    """A release starts its processes minutes apart, and each asks the planet
    for its weather.

    Counted from the moment of the **ask**, the two would land on two different
    hours, both would pass the dedup key, and the planet would carry two
    independent chains of eruptions -- each queueing its own next one, and the
    ground shaking twice as often after every release, for ever. Counted from
    the start of the day, the two compute the same hour and the key makes one
    job of them.

    Asked of `schedule` rather than of `ensure_scheduled`: the guard above it
    would hide the arithmetic, and it is the arithmetic that is wrong here.
    """
    morning = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    await plates.schedule(session, constants, after=morning)
    await plates.schedule(session, constants, after=morning + timedelta(minutes=37))
    queued = (
        (await session.execute(select(Job).where(Job.kind == JobKind.PLATES_WARN.value)))
        .scalars()
        .all()
    )
    assert len(queued) == 1, "у планеты одни часы, а не по одним на процесс"


async def test_a_deploy_the_next_day_does_not_start_a_second_chain(
    session: AsyncSession, constants: Constants
) -> None:
    """The day changes, and the arithmetic alone stops covering us: a fresh day
    gives a fresh hour and a fresh key. What holds then is the chain already
    running -- a warning is pending, so there is nothing to start."""
    await plates.ensure_scheduled(session, now=datetime(2026, 9, 1, 9, 0, tzinfo=UTC))
    await plates.ensure_scheduled(session, now=datetime(2026, 9, 2, 9, 0, tzinfo=UTC))
    queued = (
        (await session.execute(select(Job).where(Job.kind == JobKind.PLATES_WARN.value)))
        .scalars()
        .all()
    )
    assert len(queued) == 1


async def test_a_second_already_used_does_not_stop_the_weather(
    session: AsyncSession, constants: Constants
) -> None:
    """The dedup key is unique across every state, so a **finished** warning of
    that same second refuses the new one.

    Two opposite reasons hide behind one refusal: usually it is the other
    process of the same deploy, a second ahead, and its job is the one we
    wanted. But a corpse holding the second is the other, and swallowing that
    refusal would stop the planet's weather until somebody restarted the world.
    """
    day = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    await plates.schedule(session, constants, after=day)
    first = await session.scalar(select(Job).where(Job.kind == JobKind.PLATES_WARN.value).limit(1))
    assert first is not None
    #: The chain ran and finished. The second it sat on stays taken for ever.
    first.state = JobState.DONE
    await session.flush()

    await plates.schedule(session, constants, after=day)
    alive = (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.PLATES_WARN.value, Job.state == JobState.PENDING
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(alive) == 1, "секунда, занятая покойником, не должна выключать погоду"
    assert alive[0].run_at > first.run_at


async def test_a_chain_that_died_does_not_stop_the_weather(
    session: AsyncSession, constants: Constants
) -> None:
    """Only a **pending** warning counts as a running chain (D-197).

    A warning that failed all its attempts is not a chain: taken for one, it
    would switch the planet's weather off for ever -- the ground would never
    move again, and the whole measure against a staked claim would quietly go
    with it.
    """
    await plates.ensure_scheduled(session, now=datetime(2026, 9, 1, 9, 0, tzinfo=UTC))
    dead = await session.scalar(select(Job).where(Job.kind == JobKind.PLATES_WARN.value).limit(1))
    assert dead is not None
    dead.state = JobState.FAILED
    await session.flush()

    await plates.ensure_scheduled(session, now=datetime(2026, 9, 3, 9, 0, tzinfo=UTC))
    alive = (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.PLATES_WARN.value, Job.state == JobState.PENDING
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(alive) == 1, "погибшая цепочка не должна выключать погоду планеты"


async def test_the_warning_outlives_the_second_it_was_said_in(
    session: AsyncSession, constants: Constants
) -> None:
    """The window is six hours wide, and the signal is an event -- and an event
    reaches whoever is connected in the second it is written (`api.push`).

    Somebody logging in ten minutes into the window would otherwise stand on
    ground about to move and read nothing about it. The place carries the
    warning while it stands, so `look` shows it (D-197, P6, D-225: the client
    cannot derive an announced hour from anything it already has).
    """
    from src.api.commands.look import _look

    #: Eight fields against at most `pyroxis.nodes_shifted` shaken: a quiet one
    #: is then certain, and the half of the contract about the **absent** key is
    #: checked on every run rather than on the runs where the dice were kind.
    _, fields = await _surface(session, count=8)
    await plates.ensure_scheduled(session, now=datetime.now(UTC))
    warning = await session.scalar(
        select(Job).where(Job.kind == JobKind.PLATES_WARN.value).limit(1)
    )
    assert warning is not None
    await plates.warned(session, warning)
    coming = await session.scalar(
        select(Job).where(Job.kind == JobKind.PLATES_ERUPT.value).limit(1)
    )
    assert coming is not None
    shaken = {uuid.UUID(one) for one in coming.payload["nodes"]}

    for field in fields:
        said = await plates.shaking(session, field)
        assert (said is not None) == (field.id in shaken)
        if said is not None:
            assert said == pytest.approx(coming.run_at, abs=timedelta(seconds=1))

    #: And it is on the look of somebody standing there, not only in the second
    #: the signal was written.
    where = next(field for field in fields if field.id in shaken)
    body = await _dweller(session, where)
    seen = (await _look({"identity_id": body.identity_id}, session, {}))["look"]
    assert seen["node"]["shaking_at"] == coming.run_at.isoformat()

    #: A quiet field says nothing at all: an absent key, not a null.
    calm = next(field for field in fields if field.id not in shaken)
    quiet = await _dweller(session, calm)
    seen = (await _look({"identity_id": quiet.identity_id}, session, {}))["look"]
    assert "shaking_at" not in seen["node"]


async def test_the_digest_tells_what_the_place_lived_through(
    session: AsyncSession, constants: Constants
) -> None:
    """Coming back to a changed map, one must be told why it changed (D-197).

    An eruption has no actor -- it is the planet's doing -- so it is asked for
    by the **node** the body stands in, and merged into the digest by time
    rather than appended after everything the player did themselves.
    """
    from src.api.commands.world import _world_summary

    _, fields = await _surface(session, count=2)
    where = fields[0]
    body = await _dweller(session, where)
    now = datetime.now(UTC)

    #: Two of the place's own, an hour apart, and the older one written last:
    #: two lists each sorted by itself do not make one sorted list.
    #: One of the player's own, and **older** than both of the planet's: the two
    #: lists are each sorted by themselves, so only an event that has to move
    #: between them can tell a merge from a concatenation.
    session.add(
        Event(
            kind=EventKind.TRAVEL_ARRIVED.value,
            actor_identity_id=body.identity_id,
            node_id=where.id,
            at=now - timedelta(hours=3),
        )
    )
    for hours, kind in ((2, EventKind.PLATES_WARNED), (1, EventKind.PLATES_ERUPTED)):
        #: Written by hand rather than through `events.record`: the journal is
        #: append-only and refuses to have its stamp moved afterwards, and the
        #: whole question here is the order of two stamps.
        session.add(Event(kind=kind.value, node_id=where.id, at=now - timedelta(hours=hours)))
    await session.flush()

    digest = await _world_summary(
        {"identity_id": body.identity_id}, session, {"since": (now - timedelta(days=1)).isoformat()}
    )
    told = [line["kind"] for line in digest["happened"]]
    assert [one for one in told if one.startswith("plates.")] == [
        EventKind.PLATES_ERUPTED.value,
        EventKind.PLATES_WARNED.value,
    ]
    #: Newest first across **everything**, the player's own doings included:
    #: the body was printed just now and stands above an eruption of an hour ago.
    assert told[0] == EventKind.BODY_PRINTED.value
    assert told[-1] == EventKind.TRAVEL_ARRIVED.value
    assert [line["at"] for line in digest["happened"]] == sorted(
        (line["at"] for line in digest["happened"]), reverse=True
    )

    #: And nothing of a place one is not standing in.
    elsewhere = await _dweller(session, fields[1])
    quiet = await _world_summary(
        {"identity_id": elsewhere.identity_id},
        session,
        {"since": (now - timedelta(days=1)).isoformat()},
    )
    assert not [line for line in quiet["happened"] if line["kind"].startswith("plates.")]


async def test_the_signal_comes_before_the_ground_moves(
    session: AsyncSession, constants: Constants
) -> None:
    """Free, to everybody in the nodes, and ahead of the loss (P6, D-197): the
    window to walk out of is not merchandise."""
    await _surface(session, count=3)
    await plates.ensure_scheduled(session, now=datetime.now(UTC))
    warning = await session.scalar(
        select(Job).where(Job.kind == JobKind.PLATES_WARN.value).limit(1)
    )
    assert warning is not None

    await plates.warned(session, warning)
    told = (
        (await session.execute(select(Event).where(Event.kind == EventKind.PLATES_WARNED.value)))
        .scalars()
        .all()
    )
    assert told, "сигнал приходит всем в затронутых узлах, и приходит заранее"
    coming = await session.scalar(
        select(Job).where(Job.kind == JobKind.PLATES_ERUPT.value).limit(1)
    )
    assert coming is not None
    ahead = (coming.run_at - warning.run_at).total_seconds() / 3600
    assert ahead == pytest.approx(constants[R.PYROXIS_ERUPTION_WARNING])

    #: And the next one is already in the journal: the planet keeps its own time.
    assert await session.scalar(
        select(Job).where(Job.kind == JobKind.PLATES_WARN.value, Job.id != warning.id).limit(1)
    )
