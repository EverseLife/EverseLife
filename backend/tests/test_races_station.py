# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A machine taken down raced against a batch queued at it (D-351).

A batch queued behind its master's running one holds no machine of its own:
it takes a free one of its name when its turn comes (D-209). The door that
takes a machine down refuses the last one a batch here still needs, but it can
only see a batch that is already there -- so the start that queues it takes
the machine's row (`craft._hold_station`), which the take-down holds too, and
one of the two waits for the other. Whichever comes second sees the first:
here the queue goes first, and the take-down, let in once it provably waits,
finds the batch and refuses. **The machine either stands with the batch
waiting for it, or it came down before there was any batch to wait.**

The method is the family's (`test_races.py`): one side holds the row, and the
other is let go only once the database says it waits on it
(`conftest._until_blocked_by`).
"""

from __future__ import annotations

import asyncio
import importlib
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from conftest import _hold_the_first
from src.constants import current, current_catalog
from src.engine import craft, station, world
from src.models.craft import BatchState, CraftBatch
from src.models.estate import Building
from src.models.identity import Body
from src.models.inventory import Item

BENCH = "workbench"
#: At the bench, out of wood: the batch that queues.
HANDLE = "handle"
#: By hand, out of fibre: the batch already running, so the next one queues.
ROPE = "rope"

#: The module whose name `start` calls the lock by -- patched there, not at home.
work = importlib.import_module("src.engine.craft.batch.work")


async def _shop(factory: async_sessionmaker):
    """One bench in a built workshop, a master already working by hand, and
    the bench's keeper beside them -- committed."""
    async with factory() as session, session.begin():
        stamp = uuid.uuid4().hex[:8]
        node = await world.create_node(session, f"terra.shop.{stamp}", "Мастерская", area_m2=200)
        session.add(Building(node_id=node.id, area_m2=200))
        await session.flush()
        yard = await world.node_container(session, node)
        bench = await world.grant_item(session, yard, BENCH, quality=60, origin="тест")
        who = await world.create_identity(session, f"Мастер-{stamp}")
        master = await world.print_body(session, who, node)
        await world.learn(session, who, HANDLE)
        await world.learn(session, who, ROPE)
        pocket = await world.body_container(session, master)
        await world.grant_item(session, pocket, "wood", amount=50, quality=60, origin="тест")
        await world.grant_item(session, pocket, "fiber", amount=10, quality=60, origin="тест")
        running = await craft.start(session, current(), current_catalog(), master, ROPE, 1)
        assert running.state is BatchState.RUNNING
        keeper = await world.print_body(
            session, await world.create_identity(session, f"Хозяин-{stamp}"), node
        )
        return master.id, keeper.id, bench.id


async def test_a_bench_taken_down_while_a_batch_queued_at_it_is_refused(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    master_id, keeper_id, bench_id = await _shop(factory)
    held = _hold_the_first(monkeypatch, factory, work, "_hold_station")

    async def queue() -> CraftBatch:
        async with factory() as db, db.begin():
            me = await db.get(Body, master_id)
            assert me is not None
            return await craft.start(db, current(), current_catalog(), me, HANDLE, 1)

    async def take_down() -> Item:
        await held.wait()
        async with factory() as db, db.begin():
            me = await db.get(Body, keeper_id)
            bench = await db.get(Item, bench_id)
            assert me is not None and bench is not None
            return await station.take(db, current_catalog(), me, bench)

    queued, taken = await asyncio.gather(queue(), take_down(), return_exceptions=True)

    assert isinstance(queued, CraftBatch), queued
    assert queued.state is BatchState.WAITING, "за мастером уже идёт партия руками"
    assert isinstance(taken, station.Busy), taken
    assert taken.key == "station-batch-waits", taken.key
    async with factory() as db:
        bench = await db.get(Item, bench_id)
        assert bench is not None and bench.installed, "станок стоит, партия его ждёт"
