# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Reads write nothing and wait for nothing.

The rule from the review of 2026-08-23 ("чтение не пишет"), pinned from both
sides: `look` and the forecasts leave no row behind -- no account, no yard, no
stall -- and they answer while a real action holds the body's lock, because a
read queued behind a write is the wrong way round. The races proper live in
`test_races*.py`; these tests share their origin, not their technique.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.engine import ledger, market, world
from src.models.ledger import AccountKind


async def test_reads_do_not_write(session: AsyncSession) -> None:
    """`look`-class reads create nothing: no account for a fresh identity, no
    container for an untouched chest, no stall in a node never traded in."""
    from src.engine import storage

    identity = await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:6]}")
    assert await ledger.find_account(session, AccountKind.IDENTITY, identity.id) is None
    node = await world.create_node(
        session, f"terra.quiet.{uuid.uuid4().hex[:6]}", "Тихо", area_m2=100
    )
    assert await market.stall(session, node, identity.id, create=False) is None
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    chest = await world.grant_item(session, pocket, "chest", quality=50, origin="тест")
    assert await storage.content(session, chest) == []
    assert await storage.inside(session, chest, create=False) is None
    assert await storage.is_empty(session, chest)


async def test_look_writes_nothing(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    """A fresh identity in a fresh node: `look` creates no account, no
    container, no row of any kind (review 2026-08-23).

    The node is stripped of its yard first: `create_node` has made one with the
    node since that review, and a yard already there hides the leak this test
    is for -- the scene reached the yard through the **creating**
    `node_container`, so a look at a node from an old world wrote to it.
    """
    from sqlalchemy import delete, func

    from src.api.commands import look as api
    from src.models.inventory import Container, ContainerKind
    from src.models.ledger import LedgerAccount

    node = await world.create_node(
        session, f"terra.fresh.{uuid.uuid4().hex[:6]}", "Тихо", area_m2=100
    )
    identity = await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:6]}")
    await world.print_body(session, identity, node)
    await session.execute(
        delete(Container).where(Container.kind == ContainerKind.NODE, Container.owner_id == node.id)
    )
    await session.commit()

    async def count(db: AsyncSession) -> tuple[int, int]:
        containers = await db.scalar(select(func.count()).select_from(Container))
        accounts = await db.scalar(select(func.count()).select_from(LedgerAccount))
        return containers, accounts

    before = await count(session)
    async with factory() as db, db.begin(), _writes_forbidden(db):
        await api._look({"identity_id": identity.id}, db, {"cmd": "look"})
    assert await count(session) == before


@contextlib.asynccontextmanager
async def _writes_forbidden(db: AsyncSession) -> AsyncIterator[None]:
    """Fail on any flush of this session -- the only honest way to say "wrote
    nothing".

    `db.new` / `db.dirty` / `db.deleted` are empty **after** a flush: the row
    is persistent by then and the session is clean again. So the very shape
    the yard used to be created in -- `session.add` followed by
    `await session.flush()` -- passes those three checks in silence. The
    listener sees the flush itself, whoever caused it.
    """
    caught: list[str] = []

    def watch(sync_session, context) -> None:
        caught.extend(
            repr(row) for row in (*sync_session.new, *sync_session.dirty, *sync_session.deleted)
        )

    event.listen(db.sync_session, "after_flush", watch)
    try:
        yield
    finally:
        event.remove(db.sync_session, "after_flush", watch)
    assert not caught, caught


async def _forecaster(session: AsyncSession, name: str) -> uuid.UUID:
    """A master on an empty plot with a forge in the yard: both forecasts have
    everything they need -- a bill to count and a batch to price."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.{name}.{stamp}", "Мастерская", area_m2=100)
    identity = await world.create_identity(session, f"Зодчий-{stamp}")
    body = await world.print_body(session, identity, node)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "forge", quality=60, origin="тест")
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "iron_ingot", amount=10, quality=80, origin="тест")
    await world.learn(session, identity, "nails")
    await session.commit()
    return identity.id


async def test_forecasts_write_nothing(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    """The forecasts answer what *would* happen: they spend nothing and reserve
    nothing (D-092), so their transaction must have nothing to write --
    "чтение не пишет"."""
    from src.api.commands.craft import _craft_plan
    from src.api.commands.estate import _build_estimate

    who = await _forecaster(session, "bill")

    async with factory() as db, db.begin(), _writes_forbidden(db):
        bill = await _build_estimate({"identity_id": who}, db, {"area": 20, "floors": 1})
        assert bill["materials"], bill
    async with factory() as db, db.begin(), _writes_forbidden(db):
        plan = await _craft_plan({"identity_id": who}, db, {"output": "nails", "units": 3})
        assert plan["plan"]["consumes"], plan


async def test_forecasts_make_no_yard_in_a_place_without_one(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    """A node from an old world has no yard row, and a forecast must not
    give it one.

    `create_node` has made the yard with the node since the review of
    2026-08-23, but `node_container` still catches the nodes born before
    that -- by **creating** the yard, wherever it is asked from. The demolition
    bill counts the slots and what lies on the floor, the batch looks for its
    machine, and the scene lists what stands here: each of them went through
    it. An INSERT behind every keystroke of the workshop's quantity field --
    the refusal "no such machine here" included -- and behind every `look`.
    """
    from sqlalchemy import delete, func

    from src.api.commands.craft import _craft_plan
    from src.api.commands.estate import _build_estimate, _demolish_estimate, _repair_estimate
    from src.api.commands.look import _look
    from src.engine import craft
    from src.models.inventory import Container, ContainerKind

    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.bare.{stamp}", "Пустошь", area_m2=100)
    identity = await world.create_identity(session, f"Гость-{stamp}")
    await world.print_body(session, identity, node)
    await world.learn(session, identity, "nails")
    #: A node as an old world left it: everything else is there, the yard is not.
    await session.execute(
        delete(Container).where(Container.kind == ContainerKind.NODE, Container.owner_id == node.id)
    )
    await session.commit()
    who = {"identity_id": identity.id}

    async def yards() -> int:
        return await session.scalar(
            select(func.count())
            .select_from(Container)
            .where(Container.kind == ContainerKind.NODE, Container.owner_id == node.id)
        )

    assert await yards() == 0, "двор снесён -- это узел старого мира"

    async with factory() as db, db.begin(), _writes_forbidden(db):
        await _look(who, db, {"cmd": "look"})
        await _build_estimate(who, db, {"area": 20, "floors": 1})
        await _demolish_estimate(who, db, {})
        await _repair_estimate(who, db, {})
        #: The forge is not here, so the plan refuses -- and the refusal is
        #: exactly the path that used to leave a yard behind.
        with pytest.raises(craft.NoStation):
            await _craft_plan(who, db, {"output": "nails", "units": 3})
    assert await yards() == 0, "прогноз завёл двор там, где только смотрели"


async def test_forecast_does_not_wait_for_the_body_lock(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    """A forecast must answer while the body's own action holds its row.

    The client counts the bill as the player types (300 ms after the last
    keystroke), and under `_alive` every one of those reads would queue behind
    whatever the body is doing -- a read delaying a write, which is the wrong
    way round.
    """
    from src.api.commands import common as api
    from src.api.commands.craft import _craft_plan
    from src.api.commands.estate import _build_estimate

    who = await _forecaster(session, "lockfree")

    async def bill() -> dict:
        async with factory() as db, db.begin():
            return await _build_estimate({"identity_id": who}, db, {"area": 20, "floors": 1})

    async def plan() -> dict:
        async with factory() as db, db.begin():
            return await _craft_plan({"identity_id": who}, db, {"output": "nails", "units": 3})

    async with factory() as holder, holder.begin():
        #: The lock a real action takes and keeps for its whole transaction.
        await api._alive({"identity_id": who}, holder)
        counted = await asyncio.wait_for(bill(), timeout=5)
        priced = await asyncio.wait_for(plan(), timeout=5)
    assert counted["materials"], counted
    assert priced["plan"]["consumes"], priced
