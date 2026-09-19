# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The orphan sweep raced against the master walking away (D-217, D-209).

One of the race files (see `test_races.py` for the family's method). Two doors
close a running batch whose job is dead: the tick's sweep, which gives back
what went in and cancels it (`craft.queue._abandon`), and `craft.freeze`,
which stops it with its time left when the master leaves. Before the sweep
took the master's row the two passed each other: the freeze read the batch
still running, the sweep gave the materials back and cancelled it, and the
freeze's write, let through at the sweep's commit, put it back to waiting --
a work on materials already returned, which the master's return then
finished. Now they queue on the body's row, as the batch's own end and every
door into a pair of hands do, and whichever comes second sees what the first
did.

Only the side that locks first is catchable (the family's rule): here, the
sweep holding the row while the freeze waits, and the freeze -- any command --
holding it while the sweep comes by. The second one the sweep does not wait
out: it leaves the batch to the next tick. And two more, which no lock alone
answers: the master walking away -- or away and straight back -- between the
sweep reading its orphans and taking the row; the batch has to be read again
under it, its state and its run both.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _hold_the_first
from src.constants import current, current_catalog
from src.engine import craft, world
from src.engine.craft import queue
from src.models.craft import BatchState, CraftBatch
from src.models.estate import Building
from src.models.identity import Body
from src.models.inventory import Item
from src.models.job import Job, JobState
from src.units import amount_float

WOOD = "wood"
HELD = 50


async def _orphan(factory: async_sessionmaker[AsyncSession]):
    """A master at the bench with a batch whose job has died -- committed."""
    async with factory() as session, session.begin():
        stamp = uuid.uuid4().hex[:8]
        node = await world.create_node(session, f"terra.orphan.{stamp}", "Yard", area_m2=200)
        session.add(Building(node_id=node.id, area_m2=200))
        await session.flush()
        yard = await world.node_container(session, node)
        await world.grant_item(session, yard, "workbench", quality=60, origin="test")
        identity = await world.create_identity(session, f"Master-{stamp}")
        body = await world.print_body(session, identity, node)
        await world.learn(session, identity, "handle")
        pocket = await world.body_container(session, body)
        await world.grant_item(session, pocket, WOOD, amount=HELD, quality=60, origin="test")
        batch = await craft.start(session, current(), current_catalog(), body, "handle", 2)
        job = (
            await session.execute(select(Job).where(Job.dedup_key == f"craft.batch:{batch.id}"))
        ).scalar_one()
        #: What a defect does after the retries run out.
        job.state = JobState.FAILED
        return body.id, batch.id, float(batch.spent[WOOD])


async def _wood(factory: async_sessionmaker[AsyncSession], body_id) -> float:
    async with factory() as db:
        pocket = await world.body_container(db, await db.get(Body, body_id))
        rows = await db.execute(
            select(Item).where(Item.container_id == pocket.id, Item.type_key == WOOD)
        )
        return sum(amount_float(row.amount) for row in rows.scalars())


async def _batch(factory: async_sessionmaker[AsyncSession], batch_id) -> CraftBatch:
    async with factory() as db:
        batch = await db.get(CraftBatch, batch_id)
        assert batch is not None
        return batch


async def _sweep(factory: async_sessionmaker[AsyncSession]) -> queue.Swept:
    async with factory() as db, db.begin():
        return await craft.sweep_orphans(db)


async def test_a_master_walking_away_mid_sweep_does_not_revive_the_batch(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sweep holds the master's row; the master walks away meanwhile.

    The freeze waits for the sweep and then finds nothing running: the batch
    stays cancelled and the wood came back once. Held at `wake_node`, the
    sweep's last step, after the batch's cancelled row is written -- the
    point the old sweep, without the body's row, also reached: there the
    freeze walked in, read the batch still running and waited only on its
    write, which the sweep's commit then let through.
    """
    body_id, batch_id, _ = await _orphan(factory)
    held = _hold_the_first(monkeypatch, factory, queue, "wake_node")

    async def leave() -> CraftBatch | None:
        """A command's shape: the body's row first, the freeze after it."""
        await held.wait()
        async with factory() as db, db.begin():
            who = (
                await db.execute(select(Body).where(Body.id == body_id).with_for_update())
            ).scalar_one()
            return await craft.freeze(db, who)

    swept, frozen = await asyncio.gather(_sweep(factory), leave())

    assert swept == (1, 0)
    assert frozen is None, "the freeze found the batch already closed"
    assert (await _batch(factory, batch_id)).state is BatchState.CANCELLED
    assert await _wood(factory, body_id) == pytest.approx(HELD), "given back once"


async def test_a_master_mid_command_is_swept_next_tick(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """A command holds the master's row -- here, one walking away. The sweep
    does not wait on it: it leaves the batch to the next tick, and the freeze
    that comes next makes it a waiting batch, which is no orphan (D-209) --
    the materials stay in the work, not in the hands as well."""
    body_id, batch_id, spent = await _orphan(factory)

    async with factory() as mine, mine.begin():
        who = (
            await mine.execute(select(Body).where(Body.id == body_id).with_for_update())
        ).scalar_one()
        #: Bounded: a sweep that queues on the row would wait on this very
        #: transaction for ever, and the test would hang rather than fail.
        try:
            swept = await asyncio.wait_for(_sweep(factory), timeout=10)
        except TimeoutError:
            pytest.fail("the sweep queued on a busy master's row")
        assert swept == (0, 1), "a busy master is left to the next tick, and said so"
        assert await craft.freeze(mine, who) is not None

    assert await _sweep(factory) == (0, 0)
    batch = await _batch(factory, batch_id)
    assert batch.state is BatchState.WAITING
    assert batch.remaining_seconds is not None and float(batch.remaining_seconds) > 0
    assert await _wood(factory, body_id) == pytest.approx(HELD - spent)


def _between_look_and_lock(monkeypatch: pytest.MonkeyPatch) -> tuple[asyncio.Event, asyncio.Event]:
    """Stop the sweep after it has read its orphans and before it takes the
    master's row: `looked` says it is there, and it goes on once `go` is set.
    What the other side commits in between is what the sweep has to see."""
    looked, go = asyncio.Event(), asyncio.Event()
    master = queue._master

    async def late(session: AsyncSession, batch: CraftBatch) -> Body | None:
        looked.set()
        await go.wait()
        return await master(session, batch)

    monkeypatch.setattr(queue, "_master", late)
    return looked, go


async def test_a_batch_frozen_after_the_sweep_looked_is_left_waiting(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sweep reads its orphans before it takes anybody's row. A master who
    walks away in between -- the freeze taking the row, stopping the batch and
    committing -- leaves the sweep a batch that is no longer running: read
    again under the row, it is a waiting batch, not an orphan (D-209), and the
    sweep leaves it and its materials alone. Read from before the lock, it
    would cancel the work and pay out the wood of a batch still owed its
    hours."""
    body_id, batch_id, spent = await _orphan(factory)
    looked, go = _between_look_and_lock(monkeypatch)

    async def leave() -> None:
        await looked.wait()
        async with factory() as db, db.begin():
            who = (
                await db.execute(select(Body).where(Body.id == body_id).with_for_update())
            ).scalar_one()
            assert await craft.freeze(db, who) is not None
        go.set()

    swept, _ = await asyncio.gather(_sweep(factory), leave())

    assert swept == (0, 0)
    assert (await _batch(factory, batch_id)).state is BatchState.WAITING
    assert await _wood(factory, body_id) == pytest.approx(HELD - spent)


async def test_a_batch_taken_up_again_after_the_sweep_looked_is_left_running(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same window, and the master steps away and back in it: the batch
    froze and was taken up again, so it is running once more -- as the sweep
    saw it -- but on its second run, with a live job of its own. The state
    alone cannot tell it from the orphan the sweep read; the run can. Without
    it the sweep would cancel a healthy batch and pay its wood out, and the
    new run's job would find nothing to finish."""
    body_id, batch_id, spent = await _orphan(factory)
    looked, go = _between_look_and_lock(monkeypatch)

    async def away_and_back() -> None:
        await looked.wait()
        async with factory() as db, db.begin():
            who = (
                await db.execute(select(Body).where(Body.id == body_id).with_for_update())
            ).scalar_one()
            assert await craft.freeze(db, who) is not None
            assert await craft.wake(db, who) is not None
        go.set()

    swept, _ = await asyncio.gather(_sweep(factory), away_and_back())

    assert swept == (0, 0)
    batch = await _batch(factory, batch_id)
    assert batch.state is BatchState.RUNNING
    assert batch.runs == 2
    assert await _wood(factory, body_id) == pytest.approx(HELD - spent)
