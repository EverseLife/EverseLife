# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Waiting batches taken up (D-209) raced for the machines of a node (D-150).

A batch taken up again -- its master back at the bench, a work ended beside
it, the tick's orphan sweep freeing a machine -- takes the best free machine
of its name in the node (`craft.queue._run`). One machine, one worker. Only
the door that starts a new batch took the machine's row (`_hold_station`,
D-351); the resume read the benches with a plain `SELECT` and wrote itself
onto the one it chose, so two masters taking up their work at once both went
to the one bench, the second's write over the first's -- two batches running
at a bench recorded as one master's, and the next bench standing idle.

Raced here both ways in: two sessions choosing at once -- with one bench, and
with a second one the later master should go to -- and one transaction that
read the bench earlier, as the sweep does orphan after orphan, and went on
trusting what it read: free after another master took it, busy after it came
free -- and busy with the very master it goes back to, which a write through
the remembered object would leave out.

The take passes a bench another transaction holds by instead of waiting on it
(`craft.queue._take_station`), so the handshake of the family
(`conftest._until_blocked_by`) is used with `unless`: on the code this catches
the later side waits on the first side's row and is let go once it does; on
the fixed code it does not wait at all.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from conftest import _until_blocked_by
from src.constants import current, current_catalog
from src.engine import craft, world
from src.models.craft import BatchState, CraftBatch
from src.models.estate import Building
from src.models.identity import Body
from src.models.inventory import Item

BENCH = "workbench"
MAKE = "handle"


async def _shop(factory: async_sessionmaker, *qualities: int):
    """Benches of these qualities in a built workshop -- the first the best --
    and two masters beside them, each with a batch frozen half-way (D-209).
    Committed, every bench free."""
    async with factory() as session, session.begin():
        stamp = uuid.uuid4().hex[:8]
        node = await world.create_node(session, f"terra.resume.{stamp}", "Мастерская", area_m2=200)
        session.add(Building(node_id=node.id, area_m2=200))
        await session.flush()
        yard = await world.node_container(session, node)
        benches = [
            await world.grant_item(session, yard, BENCH, quality=quality, origin="тест")
            for quality in qualities or (60,)
        ]
        masters = []
        for n in range(2):
            who = await world.create_identity(session, f"Мастер-{n}-{stamp}")
            body = await world.print_body(session, who, node)
            await world.learn(session, who, MAKE)
            pocket = await world.body_container(session, body)
            await world.grant_item(session, pocket, "wood", amount=50, quality=60, origin="тест")
            batch = await craft.start(session, current(), current_catalog(), body, MAKE, 1)
            assert batch.state is BatchState.RUNNING
            assert await craft.freeze(session, body) is batch
            masters.append(body.id)
        for bench in benches:
            await session.refresh(bench)
            assert bench.busy_body_id is None, "both frozen: every bench is free"
        return masters[0], masters[1], [bench.id for bench in benches]


async def _wake(db, body_id) -> CraftBatch | None:
    """What a master's own command does: their row, then the waiting work."""
    me = await db.get(Body, body_id, with_for_update=True)
    assert me is not None
    return await craft.wake(db, me)


async def _batch(factory: async_sessionmaker, body_id) -> CraftBatch:
    """The master's one batch, read fresh."""
    async with factory() as db:
        return (
            await db.execute(select(CraftBatch).where(CraftBatch.body_id == body_id))
        ).scalar_one()


async def _whose(factory: async_sessionmaker, bench_id):
    """Who the bench is recorded as busy with, read fresh."""
    async with factory() as db:
        bench = await db.get(Item, bench_id)
        assert bench is not None
        return bench.busy_body_id


async def _both_at_once(factory: async_sessionmaker, first, second) -> CraftBatch | None:
    """The first master takes up their work and holds it uncommitted while the
    second does the same; what the second got."""

    async def take_up() -> CraftBatch | None:
        async with factory() as db, db.begin():
            return await _wake(db, second)

    async with factory() as db:
        async with db.begin():
            assert await _wake(db, first) is not None, "a bench was free"
            late = asyncio.create_task(take_up())
            await _until_blocked_by(factory, db, unless=late)
        return await late


async def test_two_masters_do_not_both_take_up_their_work_at_one_free_bench(
    factory: async_sessionmaker,
) -> None:
    first, second, (bench,) = await _shop(factory)

    resumed = await _both_at_once(factory, first, second)

    assert resumed is None, "the bench is the first master's: the second waits"
    assert (await _batch(factory, first)).state is BatchState.RUNNING
    assert (await _batch(factory, second)).state is BatchState.WAITING
    assert await _whose(factory, bench) == first, "the bench is recorded as the first master's"


async def test_the_second_master_goes_to_the_next_bench(factory: async_sessionmaker) -> None:
    """Two benches: the first master takes the better one, and the second,
    finding it held, goes to the other instead of onto the first's."""
    first, second, (better, worse) = await _shop(factory, 60, 40)

    resumed = await _both_at_once(factory, first, second)

    assert resumed is not None, "the second bench was free"
    assert (await _batch(factory, first)).station_item_id == better
    assert (await _batch(factory, second)).station_item_id == worse
    assert await _whose(factory, better) == first
    assert await _whose(factory, worse) == second


async def test_a_bench_read_free_earlier_in_the_transaction_is_not_taken_from_its_new_master(
    factory: async_sessionmaker,
) -> None:
    """The sweep's way (D-217): one transaction frees machines and wakes whoever
    waits, orphan after orphan, and a bench it read free for an early orphan
    stays in its memory as free. Another master takes the bench and commits;
    the later wake must ask the row, not the memory."""
    first, second, (bench,) = await _shop(factory)

    async with factory() as sweep, sweep.begin():
        seen = await sweep.get(Item, bench)
        assert seen is not None and seen.busy_body_id is None
        async with factory() as other, other.begin():
            assert await _wake(other, second) is not None
        assert await _wake(sweep, first) is None, "the bench went to the second master"

    assert (await _batch(factory, first)).state is BatchState.WAITING
    assert (await _batch(factory, second)).state is BatchState.RUNNING
    assert await _whose(factory, bench) == second, "the bench stays the second master's"


async def test_a_bench_read_busy_earlier_in_the_transaction_is_taken_once_it_came_free(
    factory: async_sessionmaker,
) -> None:
    """The other side of the same memory: the bench read busy, its master walks
    away and it comes free (`craft.freeze`), and the later wake takes it rather
    than leaving the batch waiting at a free bench."""
    first, second, (bench,) = await _shop(factory)
    async with factory() as db, db.begin():
        assert await _wake(db, second) is not None

    async with factory() as sweep, sweep.begin():
        seen = await sweep.get(Item, bench)
        assert seen is not None and seen.busy_body_id == second
        async with factory() as other, other.begin():
            away = await other.get(Body, second)
            assert away is not None
            assert await craft.freeze(other, away) is not None
        assert await _wake(sweep, first) is not None, "the bench came free"

    assert (await _batch(factory, first)).state is BatchState.RUNNING
    assert (await _batch(factory, second)).state is BatchState.WAITING
    assert await _whose(factory, bench) == first


async def test_a_master_back_at_the_bench_they_left_is_recorded_on_it(
    factory: async_sessionmaker,
) -> None:
    """The same memory with the same master in it: the bench read busy with
    the first master's work, the master walks away and it comes free, and the
    sweep's later wake gives it back to them. The hold is written through the
    object, and an object still remembering this master would take the new
    `busy_body_id` for no change and leave it out of the write -- the bench
    held by nobody, free for the next master to take."""
    first, _, (bench,) = await _shop(factory)
    async with factory() as db, db.begin():
        assert await _wake(db, first) is not None

    async with factory() as sweep, sweep.begin():
        seen = await sweep.get(Item, bench)
        assert seen is not None and seen.busy_body_id == first
        async with factory() as other, other.begin():
            away = await other.get(Body, first)
            assert away is not None
            assert await craft.freeze(other, away) is not None
        assert await _wake(sweep, first) is not None, "the bench came free"

    assert (await _batch(factory, first)).state is BatchState.RUNNING
    assert await _whose(factory, bench) == first, "the bench is held by its master again"
