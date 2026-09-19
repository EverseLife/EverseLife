# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A melt of coins raced against a hand dropping coins off the same stack.

One of the race files (see `test_races.py` for the family's method). The melt
writes the stack off the moment it starts (`coin.melt`), and the stack is
money: it is changed only under its row's lock (CLAUDE.md). Two transactions
take coins off one pocket stack at once -- the melt and a drop to the floor
(`world.move_stack`, the one door every move goes through) -- and whichever
takes the row first, **every coin is in exactly one place afterwards**: in the
pocket, on the floor or in the melt. Before the lock the melt subtracted from
the count it read before the drop, wrote it over the drop's, and the dropped
coins existed twice; a whole stack dropped meanwhile was melted off the floor.

Neither side takes the body's row. A command does (`api.commands.common._alive`),
and in the command path that row queues most doors into the same hands -- a
hand-over, a batch paying out -- though not every one: the orphan sweep returns
a melt's coins into the pocket without it (`craft.queue._abandon`). A race that
let the body's row in would pass on the unlocked write-off, leaning on a guard
it does not name. This pins the stack's own lock, which is what the rule asks
of every write-off of money, whoever calls it.

One side holds the row, and the other is let go only once the database says
it waits on it (`conftest._until_blocked_by`), so the order is a certainty
rather than a matter of luck with the scheduler.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _until_blocked_by
from src.constants import current, current_catalog
from src.engine import coin, craft, gear, world
from src.models.craft import BatchKind, CraftBatch
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node
from src.units import amount_float

GOLD = "gold_coin"
#: Coins: in the pocket before the race, taken into the melt, dropped on the
#: floor when the drop takes part of the stack.
HELD = 10
MELTED = 3
DROPPED = 4


async def _mint(factory: async_sessionmaker[AsyncSession]):
    """A press in the yard and a minter with a stack of coins -- committed."""
    async with factory() as session, session.begin():
        stamp = uuid.uuid4().hex[:8]
        node = await world.create_node(session, f"terra.melt.{stamp}", "Mint", area_m2=100)
        yard = await world.node_container(session, node)
        await world.grant_item(session, yard, "coin_station", quality=60, origin="test")
        minter = await world.print_body(
            session, await world.create_identity(session, f"Minter-{stamp}"), node
        )
        stack = await world.grant_item(
            session, await world.body_container(session, minter), GOLD, amount=HELD, origin="test"
        )
        stack.fineness = Decimal(str(coin.fineness_of(current())))
        return node.id, minter.id, stack.id


async def _coins(factory: async_sessionmaker[AsyncSession], node_id, minter_id):
    """Coins in the pocket, on the floor and in the melt, as committed."""
    async with factory() as db:
        minter = await db.get(Body, minter_id)
        node = await db.get(Node, node_id)
        assert minter is not None and node is not None
        pocket = await world.body_container(db, minter)
        yard = await world.node_container(db, node)

        async def lying(container) -> float:
            rows = await db.execute(
                select(Item.amount).where(Item.container_id == container.id, Item.type_key == GOLD)
            )
            return sum(amount_float(one) for one in rows.scalars())

        melts = await db.execute(
            select(CraftBatch.units).where(
                CraftBatch.body_id == minter_id, CraftBatch.kind == BatchKind.RECYCLE
            )
        )
        return (
            await lying(pocket),
            await lying(yard),
            sum(amount_float(one) for one in melts.scalars()),
        )


@pytest.mark.parametrize("dropped", [DROPPED, HELD], ids=["part", "whole"])
@pytest.mark.parametrize("first", ["drop", "melt"])
async def test_a_melt_and_a_drop_off_one_stack_never_count_a_coin_twice(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    first: str,
    dropped: int,
) -> None:
    """The melt and the drop take the stack's row; the second reads what the first left.

    `drop` first: the drop holds the row it is about to split, the melt reads
    the stack before it and waits on the lock -- and melts from what the drop
    left, or is refused when the whole stack went to the floor. `melt` first:
    the melt holds the row while it looks for the press, the drop waits and
    takes what the melt left. Before the lock the melt read ten, the drop took
    four, and the melt wrote seven over the drop's six.
    """
    node_id, minter_id, stack_id = await _mint(factory)
    read = asyncio.Event()
    held = asyncio.Event()
    second: list[asyncio.Task] = []
    #: Whether the other side came to wait on the held row. Asked last, after
    #: the count: on broken code the count is what fails, and a handshake that
    #: never came would otherwise turn the race into a sequence unnoticed.
    waited: list[bool] = []

    if first == "drop":
        original = gear.require_off

        #: Asked by `move_stack` on the row it has just locked, before it
        #: splits it; the melt never asks it.
        async def hold(session, item):
            await original(session, item)
            if not held.is_set():
                held.set()
                waited.append(await _until_blocked_by(factory, session, unless=second[0]))

        monkeypatch.setattr(gear, "require_off", hold)
    else:
        machine = craft._station_item  # noqa: SLF001

        #: Asked by the melt after the stack is taken, before it is written
        #: off; the drop never asks it.
        async def hold(session, *args, **kwargs):
            found = await machine(session, *args, **kwargs)
            if not held.is_set():
                held.set()
                waited.append(await _until_blocked_by(factory, session, unless=second[0]))
            return found

        monkeypatch.setattr(craft, "_station_item", hold)

    async def melt():
        async with factory() as db, db.begin():
            minter = await db.get(Body, minter_id)
            #: Read as the command reads it (`_own_item`): a plain get, before
            #: the drop took the row.
            stack = await db.get(Item, stack_id)
            assert minter is not None and stack is not None
            read.set()
            if first == "drop":
                await held.wait()
            batch = await coin.melt(db, current(), current_catalog(), minter, stack, MELTED)
            return amount_float(batch.units)

    async def drop():
        await read.wait()
        if first == "melt":
            await held.wait()
        async with factory() as db, db.begin():
            node = await db.get(Node, node_id)
            stack = await db.get(Item, stack_id)
            assert node is not None and stack is not None
            return await world.move_stack(db, stack, await world.node_container(db, node), dropped)

    melt_task = asyncio.ensure_future(melt())
    drop_task = asyncio.ensure_future(drop())
    second.append(melt_task if first == "drop" else drop_task)
    melted, moved = await asyncio.gather(melt_task, drop_task, return_exceptions=True)

    pocket, floor, in_melt = await _coins(factory, node_id, minter_id)
    assert pocket + floor + in_melt == HELD, (
        f"every coin in one place: pocket {pocket}, floor {floor}, melt {in_melt}"
    )
    if first == "drop" and dropped == HELD:
        #: The whole stack went to the floor while the melt waited: nothing is
        #: in the hands to melt, and the floor keeps every coin.
        assert isinstance(melted, coin.CoinError), f"the melt finds no coins in hand: {melted}"
        assert melted.key == "coin-not-in-hands"
        assert (pocket, floor, in_melt) == (0, HELD, 0)
        assert waited == [True], "the melt waited on the row the drop held"
        return
    assert melted == MELTED, f"the melt went through: {melted}"
    assert in_melt == MELTED
    if first == "melt":
        #: The drop took what the melt left, at most what it asked for.
        assert moved == min(dropped, HELD - MELTED), f"the drop went through: {moved}"
    else:
        assert moved == dropped, f"the drop went through: {moved}"
    assert floor == moved
    assert pocket == HELD - MELTED - moved
    assert waited == [True], "the second side waited on the row the first held"
