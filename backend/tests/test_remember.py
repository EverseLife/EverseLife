# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""One command's memory (`db/base.remember`).

The node scene used to ask the database the same thing a dozen times over: one
`look` spent a hundred and twenty queries, and the database spent microseconds
on them -- the wait was the hundred round trips. The memory removes the
repeats, and everything worth checking about it is about what it must **not**
remember:

* an answer read before a write must not survive that write;
* a thing just put into a container must be seen by whoever asks next -- the
  query that would have flushed it is exactly the query the memory skips.

Both are checked here on real containers, because a helper that quietly serves
a stale pocket is the kind of bug that shows up as a lost item.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.base import remember
from src.engine import world
from src.units import amount as to_amount

#: `counted` -- the statement meter -- comes from `conftest.py`: the budget of
#: `look` (`test_query_budget.py`) measures with the same one.


async def _place(session: AsyncSession):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.memo.{stamp}", "Двор", area_m2=100)
    identity = await world.create_identity(session, f"Хранитель-{stamp}")
    body = await world.print_body(session, identity, node)
    return node, body


async def test_repeated_question_is_asked_once(session: AsyncSession, counted):
    """The same container, asked for five times, is read once."""
    node, _ = await _place(session)
    await world.node_container(session, node)

    before = counted.count
    for _ in range(5):
        await world.contents(session, await world.node_container(session, node))
    assert counted.count == before + 1


async def test_a_write_forgets_everything(session: AsyncSession):
    """What was read before a write is not served after it."""
    node, body = await _place(session)
    pocket = await world.body_container(session, body)
    assert await world.contents(session, pocket) == ()

    await world.grant_item(session, pocket, "iron_ore", amount=3, origin="тест")

    kinds = {thing.type_key for thing in await world.contents(session, pocket)}
    assert kinds == {"iron_ore"}


async def test_a_thing_put_but_not_flushed_is_seen(session: AsyncSession):
    """The memory skips the query that would have flushed the addition -- so it
    flushes it itself. Otherwise the next reader sees an empty container."""
    from src.models.inventory import Item

    node, body = await _place(session)
    pocket = await world.body_container(session, body)
    assert await world.contents(session, pocket) == ()

    #: Added and deliberately not flushed: this is the trap the guard exists for.
    session.add(Item(container_id=pocket.id, type_key="nails", amount=to_amount(1), quality=50))

    kinds = {thing.type_key for thing in await world.contents(session, pocket)}
    assert kinds == {"nails"}


async def test_the_memory_dies_with_the_session(session: AsyncSession, database):
    """Nothing is kept between commands: a session is one command."""
    calls = {"n": 0}

    async def produce() -> str:
        calls["n"] += 1
        return "value"

    await remember(session, ("probe",), produce)
    await remember(session, ("probe",), produce)
    assert calls["n"] == 1

    #: A different session is a different command, and it knows nothing.
    async with async_sessionmaker(database, expire_on_commit=False)() as other:
        await remember(other, ("probe",), produce)
    assert calls["n"] == 2
