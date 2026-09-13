# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions over one thing's row: the lock every door takes on a thing.

One of the race files (see `test_races.py` for the family's method). The doors
themselves are raced in `test_races_gone.py` and `test_races_harness.py`; this
pins the one lock they share (`world.lock_thing`) -- what it answers when the
thing is gone, and what it rereads when the wait is over -- so that five doors
differing only in the refusal they raise cannot drift apart again.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import _until_blocked_by
from src.engine import station, storage, transport, world
from src.engine.errors import Refusal
from src.engine.world.things import ItemGone
from src.models.inventory import Item
from src.models.world import Node
from src.units import amount, amount_float

ORE = "iron_ore"
COAL = "coal"
#: Pieces: of ore in the sack as it lies and after the other hand, and of the
#: coal that other hand drops beside it -- a second stack, not folded into the sack.
BEFORE = 3
AFTER = 5
DROPPED = 2


async def _sack(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """A sack of ore on the floor of nobody's land, committed. Returns the node
    and the sack."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.lock.{stamp}", "Clearing", area_m2=200)
    yard = await world.node_container(session, node)
    sack = await world.grant_item(session, yard, ORE, amount=BEFORE, origin="test")
    node_id, sack_id = node.id, sack.id
    await session.commit()
    return node_id, sack_id


@pytest.mark.parametrize(
    "gone", [storage.StorageError, station.StationError, transport.NotHere, ItemGone]
)
async def test_a_thing_gone_before_the_lock_is_refused_by_the_door_that_asked(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    gone: type[Refusal],
) -> None:
    """The row went between the look and the lock: the door's own refusal, in words.

    The hand holds the sack it was shown; another transaction takes it out of
    the world and commits. The lock finds no row, and the answer is the
    refusal class the door passed -- so its callers catch what they always
    caught -- with the name read before the failed refresh left none.
    """
    _, sack_id = await _sack(session)

    async with factory() as db, db.begin():
        thing = await db.get(Item, sack_id)
        assert thing is not None
        async with factory() as other, other.begin():
            gone_row = await other.get(Item, sack_id)
            assert gone_row is not None
            await other.delete(gone_row)

        with pytest.raises(gone) as refused:
            await world.lock_thing(db, thing, gone=gone)

    assert type(refused.value) is gone
    assert refused.value.key == "thing-gone"
    assert refused.value.params == {"goods": ORE}


async def test_the_lock_waits_then_answers_from_the_row_and_the_world_as_they_stand(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """After the wait, both the thing and what the command remembered are read anew.

    The hand reads the sack and what lies on the floor, and the floor's answer
    is remembered (`db.base.remember`). Another transaction holds the sack's
    row, adds to it, drops a heap beside it and commits once the hand has
    walked into the lock. Answered from before the wait, the hand would count
    three pieces and one stack; answered after it, five and two.
    """
    node_id, sack_id = await _sack(session)
    held = asyncio.Event()

    async def other_hand() -> None:
        async with factory() as db, db.begin():
            sack = (
                await db.execute(select(Item).where(Item.id == sack_id).with_for_update())
            ).scalar_one()
            sack.amount = amount(AFTER)
            node = await db.get(Node, node_id)
            assert node is not None
            yard = await world.node_container(db, node)
            await world.grant_item(db, yard, COAL, amount=DROPPED, origin="test")
            await db.flush()
            held.set()
            await _until_blocked_by(factory, db)

    async def hand() -> tuple[float, int, int]:
        async with factory() as db, db.begin():
            node = await db.get(Node, node_id)
            thing = await db.get(Item, sack_id)
            assert node is not None and thing is not None
            yard = await world.node_container(db, node)
            before = len(await world.contents(db, yard))
            await held.wait()
            await world.lock_thing(db, thing, gone=ItemGone)
            return amount_float(thing.amount), before, len(await world.contents(db, yard))

    _, (counted, before, after) = await asyncio.gather(other_hand(), hand())

    assert before == 1
    assert counted == AFTER
    assert after == 2
