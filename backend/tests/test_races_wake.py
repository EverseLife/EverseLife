# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A machine that came free reaches whoever waits for it (D-209, D-211, D-217).

A batch that waits takes a free machine of its name when something wakes its
master: their own hand, their arrival, a work ended beside them
(`craft.queue.wake_node`). The wake inside the freeing transaction passes by a
master whose row is held right now -- it holds the machine that master's own
command may be reaching for, and waiting would close the circle -- and until
the second chance (`craft.queue._again`) that was the end of it: a master
waking up or walking back in the very second the bench came free found it
still busy, the finish found them held, each missed the other, and the batch
waited at a free bench until the next one came free or its master took it up
by hand. And a master walking away from a bench (`craft.freeze`) woke nobody.

Raced here: the waking master's command and the returning master's arrival
holding their row across the finish, and the second chance, which must wait
for that row rather than pass it by as the finish did -- caught by the
family's handshake (`conftest._until_blocked_by`) with `unless`: on code that
passes the row by, the job walks straight through, and the test fails on what
it did. And the walk-away, which has nothing to race, and the one master the
bench must not reach: one who went on to another occupation while the batch
waited (D-211).

The benches here come free by sleep: lying down is stepping away (D-211).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _until_blocked_by
from src.constants import current, current_catalog
from src.engine import craft, forage, jobs, rest, travel, world
from src.engine.craft import queue
from src.engine.travel import walk
from src.models.craft import BatchState, CraftBatch
from src.models.identity import Body
from src.models.inventory import Item
from src.models.job import Job, JobKind, JobState
from src.models.world import Node, Surface

BENCH = "workbench"
MAKE = "handle"


async def _shop(factory: async_sessionmaker) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, datetime]:
    """One bench, and two masters at it -- committed.

    The waiter started first and lay down (D-211): their batch is frozen and
    the bench went to the worker, whose batch runs until the moment returned.
    The waiter's batch is three times the worker's, so the job left over from
    its frozen run falls due only after the worker's finish.
    """
    async with factory() as session, session.begin():
        stamp = uuid.uuid4().hex[:8]
        node = await world.create_node(session, f"terra.wake.{stamp}", "Мастерская", area_m2=100)
        yard = await world.node_container(session, node)
        bench = await world.grant_item(session, yard, BENCH, quality=60, origin="тест")
        waiter = await _master(session, node, f"Ждёт-{stamp}")
        worker = await _master(session, node, f"Работает-{stamp}")
        frozen = await craft.start(session, current(), current_catalog(), waiter, MAKE, 3)
        assert frozen.state is BatchState.RUNNING
        await rest.sleep(session, current(), waiter)
        assert (await _batch_of(session, waiter.id)).state is BatchState.WAITING
        working = await craft.start(session, current(), current_catalog(), worker, MAKE, 1)
        assert working.state is BatchState.RUNNING, "the bench came free when the waiter lay down"
        assert working.ready_at is not None
        return waiter.id, worker.id, bench.id, working.ready_at


async def _master(session: AsyncSession, node: Node, name: str) -> Body:
    """A master who knows the handle, has wood for it and is tired enough to sleep."""
    who = await world.create_identity(session, name)
    body = await world.print_body(session, who, node)
    await world.learn(session, who, MAKE)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "wood", amount=50, quality=60, origin="тест")
    body.stamina = Decimal("1")
    await session.flush()
    return body


async def _alive(db: AsyncSession, body_id) -> Body:
    """What every command opens with (`api.commands.common._alive`): the row, held."""
    return (
        await db.execute(
            select(Body)
            .where(Body.id == body_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _batch_of(db: AsyncSession, body_id) -> CraftBatch:
    return (await db.execute(select(CraftBatch).where(CraftBatch.body_id == body_id))).scalar_one()


async def _batch(factory: async_sessionmaker, body_id) -> CraftBatch:
    """The master's one batch, read fresh."""
    async with factory() as db:
        return await _batch_of(db, body_id)


async def _whose(factory: async_sessionmaker, bench_id):
    """Who the bench is recorded as busy with, read fresh."""
    async with factory() as db:
        bench = await db.get(Item, bench_id)
        assert bench is not None
        return bench.busy_body_id


async def _second_chances(factory: async_sessionmaker, state: JobState | None = None) -> int:
    """How many second chances the journal holds, in `state` if named."""
    stmt = select(func.count()).select_from(Job).where(Job.kind == JobKind.CRAFT_WAKE.value)
    if state is not None:
        stmt = stmt.where(Job.state == state)
    async with factory() as db:
        return await db.scalar(stmt)


async def _finish_the_work(factory: async_sessionmaker, ready: datetime) -> None:
    """The worker's batch ends, in a job of its own. Run whole while a test
    holds the waiter's row: the finish passes that row by rather than waiting
    for it, so this does not hang."""
    finished = await jobs.run_one(factory, now=ready)
    assert finished is not None and finished.kind == JobKind.CRAFT_BATCH.value
    assert finished.state is JobState.DONE


async def test_a_bench_freed_while_its_waiter_wakes_up_goes_to_the_waiter(
    factory: async_sessionmaker,
) -> None:
    """The waiter wakes up (`rest.wake`) while the work at the bench is ending:
    the command finds the bench busy, the finish finds the waiter's row held.
    Once both have committed, the bench is the waiter's."""
    waiter, _, bench, ready = await _shop(factory)

    async with factory() as db, db.begin():
        await rest.wake(db, current(), await _alive(db, waiter))
        assert (await _batch_of(db, waiter)).state is BatchState.WAITING, "the bench is busy"
        await _finish_the_work(factory, ready)
    assert await _whose(factory, bench) is None, "the finish freed the bench and reached nobody"

    await jobs.run_due(factory, limit=10, now=ready)

    assert (await _batch(factory, waiter)).state is BatchState.RUNNING
    assert await _whose(factory, bench) == waiter, "the bench is the waiter's"


async def test_a_bench_freed_while_its_waiter_arrives_goes_to_the_waiter(
    factory: async_sessionmaker,
) -> None:
    """The waiter walks back to the workshop, and their arrival holds their
    row while the work at the bench ends. A body on the road still stands
    where it set out from, so the finish has to know them as on their way in,
    not only as standing here."""
    waiter, _, bench, ready = await _shop(factory)
    async with factory() as db, db.begin():
        me = await _alive(db, waiter)
        await rest.wake(db, current(), me)
        me.stamina = Decimal("50")
        shop = await db.get(Node, me.node_id)
        assert shop is not None
        there = await world.create_node(
            db, f"terra.wake.there.{uuid.uuid4().hex[:8]}", "Там", area_m2=100
        )
        await travel.connect(db, shop, there, base_seconds=30, surface=Surface.ROAD)
        gone = await travel.depart(db, current(), me, there)
        away, shop_id, there_id = gone.arrives_at, shop.id, there.id
    assert away < ready, "the road is shorter than the work"
    assert await jobs.run_one(factory, now=away) is not None
    async with factory() as db, db.begin():
        me = await _alive(db, waiter)
        assert me.node_id == there_id
        shop = await db.get(Node, shop_id)
        assert shop is not None
        home = (await travel.depart(db, current(), me, shop)).id

    async with factory() as db, db.begin():
        #: What the arrival job does, in a transaction held open here. Its row
        #: is taken the way the worker takes it, so no lane claims it twice.
        leg = (
            await db.execute(
                select(Job)
                .where(Job.kind == JobKind.TRAVEL_LEG.value, Job.state == JobState.PENDING)
                .where(Job.payload["travel"].astext == str(home))
                .with_for_update()
            )
        ).scalar_one()
        leg.state = JobState.DONE
        await walk.arrive(db, leg)
        assert (await _batch_of(db, waiter)).state is BatchState.WAITING, "the bench is busy"
        await _finish_the_work(factory, ready)

    await jobs.run_due(factory, limit=10, now=ready)

    assert (await _batch(factory, waiter)).state is BatchState.RUNNING
    assert await _whose(factory, bench) == waiter, "the bench is the waiter's"


async def test_the_second_chance_waits_for_the_waiters_row(factory: async_sessionmaker) -> None:
    """The second chance runs while the waiter's command still holds their row
    -- the master busy with something again. It waits for the row, as the
    finish could not; passing it by would leave the batch where it was."""
    waiter, _, bench, ready = await _shop(factory)

    async with factory() as db:
        async with db.begin():
            await rest.wake(db, current(), await _alive(db, waiter))
            await _finish_the_work(factory, ready)
            second = asyncio.create_task(jobs.run_one(factory, now=ready))
            assert await _until_blocked_by(factory, db, unless=second), (
                "the second chance passed the held row by"
            )
        job = await second

    assert job is not None and job.kind == JobKind.CRAFT_WAKE.value
    assert job.state is JobState.DONE
    assert (await _batch(factory, waiter)).state is BatchState.RUNNING
    assert await _whose(factory, bench) == waiter


async def test_a_master_walking_away_hands_the_bench_to_whoever_waits(
    factory: async_sessionmaker,
) -> None:
    """The worker lies down in the middle of the work (`craft.freeze`): the
    bench comes free, and the waiter standing beside it gets it -- where the
    walk-away used to wake nobody, and the bench stood idle beside a batch
    waiting for it."""
    waiter, worker, bench, ready = await _shop(factory)
    async with factory() as db, db.begin():
        await rest.wake(db, current(), await _alive(db, waiter))
    assert (await _batch(factory, waiter)).state is BatchState.WAITING

    async with factory() as db, db.begin():
        await rest.sleep(db, current(), await _alive(db, worker))
    assert await _whose(factory, bench) is None

    await jobs.run_due(factory, limit=10, now=ready)

    assert (await _batch(factory, waiter)).state is BatchState.RUNNING
    assert await _whose(factory, bench) == waiter


async def test_a_waiter_at_another_occupation_is_given_no_bench(
    factory: async_sessionmaker,
) -> None:
    """The waiter went searching the land while their batch waited -- a
    waiting batch is no occupation (D-211). The bench coming free gives them
    nothing: the work does not go on at the back of a master doing something
    else, and waits for them to take it up."""
    waiter, worker, bench, ready = await _shop(factory)
    async with factory() as db, db.begin():
        me = await _alive(db, waiter)
        await rest.wake(db, current(), me)
        me.stamina = Decimal("50")
        #: Something to find on the land, so the search can begin at all.
        shop = await db.get(Node, me.node_id)
        assert shop is not None
        shop.properties = {**(shop.properties or {}), "woods": True, "stones": True}
        await forage.start(db, current(), me)

    async with factory() as db, db.begin():
        await rest.sleep(db, current(), await _alive(db, worker))
    await jobs.run_due(factory, limit=10, now=ready)

    assert await _second_chances(factory, JobState.DONE) == 1, "the second chance came and went"
    assert (await _batch(factory, waiter)).state is BatchState.WAITING
    assert await _whose(factory, bench) is None


async def test_walking_out_of_an_empty_workshop_leaves_no_second_chance(
    factory: async_sessionmaker,
) -> None:
    """Nobody else waits here: a job for nobody would be a row in the journal
    for ever."""
    async with factory() as session, session.begin():
        stamp = uuid.uuid4().hex[:8]
        node = await world.create_node(session, f"terra.empty.{stamp}", "Мастерская", area_m2=100)
        yard = await world.node_container(session, node)
        await world.grant_item(session, yard, BENCH, quality=60, origin="тест")
        alone = await _master(session, node, f"Один-{stamp}")
        await craft.start(session, current(), current_catalog(), alone, MAKE, 1)
        await rest.sleep(session, current(), alone)

    assert await _second_chances(factory) == 0


async def test_one_second_chance_per_node_and_transaction(factory: async_sessionmaker) -> None:
    """Several machines freed in one node by one transaction -- the sweep's
    orphans, a tick's dead -- make one second chance, not one each; another
    transaction makes its own rather than waiting on this one's key."""
    _, worker, _, _ = await _shop(factory)

    async with factory() as db, db.begin():
        node_id = (await _alive(db, worker)).node_id
        await queue._again(db, node_id)
        await queue._again(db, node_id)
        async with factory() as other, other.begin():
            #: A key shared with the open transaction above would make this
            #: insert wait for it -- and this coroutine is the one that would
            #: commit it. Refused in seconds instead of hanging for ever.
            await other.execute(text("SET LOCAL lock_timeout = '2s'"))
            await queue._again(other, node_id)

    assert await _second_chances(factory) == 2
