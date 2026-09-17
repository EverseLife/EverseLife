# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A thing under the knife raced against the hand that would carry it off (D-346).

The rule of `test_taken_apart.py` said at the two moments it can be crossed:
the start, which names the thing, and the end, which takes it apart. At each a
second transaction hands the thing over at the same time. Whichever side takes
the thing's row first, the other must be refused, and the invariant holds
either way: **the thing is either taken apart in the master's hands and paid
out to them, or it lies whole in the friend's -- never both**.

The method is the family's (`test_races.py`): one side holds the row, and the
other is let go only once the database says it waits on it
(`conftest._until_blocked_by`), so the order is a certainty rather than a
matter of luck with the scheduler.
"""

from __future__ import annotations

import asyncio
import importlib
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _until_blocked_by
from src.constants import current, current_catalog
from src.engine import craft, gear, jobs, storage, world
from src.engine.errors import Refusal
from src.models.craft import BatchState, CraftBatch
from src.models.identity import Body
from src.models.inventory import Item
from src.units import amount_float

HAMMER = "hammer"
STEEL = "steel"

#: The module, not the job handler the package door exports under the same name.
ending = importlib.import_module("src.engine.craft.batch.finish")


async def _bench(factory: async_sessionmaker[AsyncSession]):
    """A forge, a master with a hammer, and a friend beside them -- committed."""
    async with factory() as session, session.begin():
        stamp = uuid.uuid4().hex[:8]
        node = await world.create_node(session, f"terra.knife.{stamp}", "Двор", area_m2=200)
        yard = await world.node_container(session, node)
        await world.grant_item(session, yard, "forge", quality=70, origin="сценарий теста")
        master = await world.print_body(
            session, await world.create_identity(session, f"Мастер-{stamp}"), node
        )
        friend = await world.print_body(
            session, await world.create_identity(session, f"Друг-{stamp}"), node
        )
        hammer = await world.grant_item(
            session,
            await world.body_container(session, master),
            HAMMER,
            quality=80,
            origin="сценарий теста",
        )
        return master.id, friend.id, hammer.id


async def _master_at_work(db: AsyncSession, master_id) -> Body:
    """The master's row taken for the transaction, as `_alive` takes it."""
    master = (
        await db.execute(
            select(Body)
            .where(Body.id == master_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return master


async def _hand(factory: async_sessionmaker[AsyncSession], master_id, friend_id, hammer_id):
    async with factory() as db, db.begin():
        giver = await db.get(Body, master_id)
        taker = await db.get(Body, friend_id)
        thing = await db.get(Item, hammer_id)
        assert giver is not None and taker is not None and thing is not None
        return await storage.hand(db, current(), current_catalog(), giver, taker, thing)


async def _held(factory: async_sessionmaker[AsyncSession], body_id, what: str) -> float:
    async with factory() as db:
        body = await db.get(Body, body_id)
        pocket = await world.body_container(db, body)
        rows = (
            await db.execute(
                select(Item).where(Item.container_id == pocket.id, Item.type_key == what)
            )
        ).scalars()
        return sum(amount_float(row.amount) for row in rows)


@pytest.mark.parametrize("first", ["end", "hand"])
async def test_a_hand_and_the_end_of_taking_apart_never_both_have_the_thing(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, first: str
) -> None:
    """The end takes the thing's row, the hand takes it too.

    `end` first: the end holds the row it is about to take apart, the hand
    waits on it and finds the thing gone. `hand` first: the hand holds the row
    and is refused on it -- the thing is under the knife -- and the end then
    takes apart what is still on the bench. Before D-346 neither side asked,
    and the hammer was taken apart in the friend's hands and paid out as steel
    to the master.
    """
    master_id, friend_id, hammer_id = await _bench(factory)
    async with factory() as session, session.begin():
        master = await session.get(Body, master_id)
        hammer = await session.get(Item, hammer_id)
        work = await craft.recycle(session, current(), current_catalog(), master, hammer)
        term, batch_id = work.ready_at, work.id

    held = asyncio.Event()
    second: list[asyncio.Task] = []

    def holding(original):
        async def hold(session, *args, **kwargs):
            result = await original(session, *args, **kwargs)
            if not held.is_set():
                held.set()
                await _until_blocked_by(factory, session, unless=second[0])
            return result

        return hold

    if first == "end":
        monkeypatch.setattr(ending, "_on_the_bench", holding(ending._on_the_bench))
    else:
        #: Asked by `move_stack` on the row it has just locked, before the knife.
        monkeypatch.setattr(gear, "require_off", holding(gear.require_off))

    async def end():
        if first == "hand":
            await held.wait()
        return await jobs.run_one(factory, now=term)

    async def hand():
        if first == "end":
            await held.wait()
        return await _hand(factory, master_id, friend_id, hammer_id)

    ending_task = asyncio.ensure_future(end())
    hand_task = asyncio.ensure_future(hand())
    second.append(hand_task if first == "end" else ending_task)
    outcomes = await asyncio.gather(ending_task, hand_task, return_exceptions=True)

    assert isinstance(outcomes[1], Refusal), f"рука уходит ни с чем: {outcomes}"
    async with factory() as db:
        batch = await db.get(CraftBatch, batch_id)
        assert batch is not None and batch.state is BatchState.DONE, "работа кончилась"
        assert await db.get(Item, hammer_id) is None, "молоток разобран"
    assert await _held(factory, friend_id, HAMMER) == 0
    assert await _held(factory, master_id, STEEL) > 0, "сталь — мастеру, за его молоток"


@pytest.mark.parametrize("first", ["start", "hand"])
async def test_a_hand_and_the_start_of_taking_apart_never_both_have_the_thing(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, first: str
) -> None:
    """The start names the thing, the hand carries it off, at the same moment.

    Without the row taken at the start, the start read the thing in the
    master's hands, the hand moved it -- nothing was under the knife yet -- and
    the batch named a hammer already in the friend's pocket. Exactly one side
    must be refused, and the end must pay only for a thing on the bench.
    """
    master_id, friend_id, hammer_id = await _bench(factory)

    held = asyncio.Event()
    second: list[asyncio.Task] = []
    original = gear.require_off

    #: Both sides ask it on the row they have just locked: the start before it
    #: writes the batch, `move_stack` before it moves the thing.
    async def hold(session, item):
        await original(session, item)
        if not held.is_set():
            held.set()
            await _until_blocked_by(factory, session, unless=second[0])

    monkeypatch.setattr(gear, "require_off", hold)

    async def start():
        if first == "hand":
            await held.wait()
        async with factory() as db, db.begin():
            #: The body's row first, as the command's door takes it
            #: (`api.commands.common._alive`) and as a handover takes both
            #: (`world.lock_bodies`). The batch's own row points at the body,
            #: and the insert rechecks that key -- so a start that took the
            #: thing first and the body second would wait on the row the
            #: handover holds while holding the row the handover wants.
            master = await _master_at_work(db, master_id)
            thing = await db.get(Item, hammer_id)
            assert thing is not None
            work = await craft.recycle(db, current(), current_catalog(), master, thing)
            return work.ready_at

    async def hand():
        if first == "start":
            await held.wait()
        return await _hand(factory, master_id, friend_id, hammer_id)

    start_task = asyncio.ensure_future(start())
    hand_task = asyncio.ensure_future(hand())
    second.append(hand_task if first == "start" else start_task)
    started, handed = await asyncio.gather(start_task, hand_task, return_exceptions=True)

    refused = [one for one in (started, handed) if isinstance(one, Refusal)]
    assert len(refused) == 1, f"одна из двух рук уходит ни с чем: {started}, {handed}"

    if isinstance(handed, Refusal):
        await jobs.run_one(factory, now=started)
        assert await _held(factory, friend_id, HAMMER) == 0
        assert await _held(factory, master_id, STEEL) > 0
    else:
        assert await _held(factory, friend_id, HAMMER) == 1, "молоток у друга, и он цел"
        async with factory() as db:
            named = await db.scalar(
                select(CraftBatch.id).where(CraftBatch.target_item_id == hammer_id)
            )
        assert named is None, "работа не названа по вещи, которой в руках нет"
