# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Forecasts write nothing and wait for nothing.

A forecast answers what *would* happen -- the building bill, the batch plan,
"as much as fits" -- and is asked while the player is still choosing, at
every keystroke of a quantity field. So it spends nothing and reserves nothing
(D-092), makes no pool, no account and no yard from a question, and answers
while the body's own action holds its row.

Cut out of `test_reads.py` (the scene, the views and the sweep of every read
as a class), which shares with it the guard and the forecaster's world
(`reads_kit.py`).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from reads_kit import _forecaster, _writes_forbidden
from src.engine import world


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


async def test_a_powered_machine_forecast_makes_no_pool(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    """A machine on electricity (D-269) asks the city pool its price for the
    forecast -- and a city that has no pool row yet must not get one from a
    question: `pool_of(create=False)` is the whole of the promise."""
    from src.api.commands.craft import _craft_plan
    from src.models.world import Layer

    stamp = uuid.uuid4().hex[:8]
    capital = await world.create_node(
        session, f"terra.volt.{stamp}", "Столица", area_m2=1, layer=Layer.PLANET
    )
    yard = await world.create_node(
        session, f"terra.volt.{stamp}.yard", "Двор", area_m2=200, layer=Layer.PLANET, parent=capital
    )
    identity = await world.create_identity(session, f"Литейщик-{stamp}")
    body = await world.print_body(session, identity, yard)
    await world.grant_item(
        session,
        await world.node_container(session, yard),
        "blast_furnace",
        quality=60,
        origin="тест",
    )
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "quartz_sand", amount=40, quality=60, origin="тест")
    await world.grant_item(session, pocket, "petroleum_coke", amount=20, quality=60, origin="тест")
    await world.learn(session, identity, "silicon")
    await session.commit()

    async with factory() as db, db.begin(), _writes_forbidden(db):
        plan = await _craft_plan(
            {"identity_id": identity.id}, db, {"output": "silicon", "units": 2}
        )
        assert plan["plan"]["energy"] > 0 and "price" in plan["plan"], plan


async def test_the_largest_batch_is_counted_without_writing(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    """ "As much as fits" (`craft.most`) reads more than the forecast does --
    the pool, the purse and the cells standing beside the machine -- and every
    one of those reads has a creating twin a keystroke away: `pool_of` makes a
    pool, `account_for` makes an account, `node_container` makes a yard.

    The button is pressed while the player is still choosing, so a write here
    would be an INSERT behind a question -- the very thing the rule forbids.
    """
    from src.api.commands.craft import _craft_most
    from src.engine.craft import power
    from src.models.world import Layer

    stamp = uuid.uuid4().hex[:8]
    capital = await world.create_node(
        session, f"terra.most.{stamp}", "Столица", area_m2=1, layer=Layer.PLANET
    )
    yard = await world.create_node(
        session, f"terra.most.{stamp}.yard", "Двор", area_m2=200, layer=Layer.PLANET, parent=capital
    )
    identity = await world.create_identity(session, f"Литейщик-{stamp}")
    body = await world.print_body(session, identity, yard)
    await world.grant_item(
        session,
        await world.node_container(session, yard),
        "blast_furnace",
        quality=60,
        origin="тест",
    )
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "quartz_sand", amount=40, quality=60, origin="тест")
    await world.grant_item(session, pocket, "petroleum_coke", amount=20, quality=60, origin="тест")
    await world.learn(session, identity, "silicon")
    await session.commit()

    async with factory() as db, db.begin(), _writes_forbidden(db):
        #: A city without a pool row and a master without an account: the
        #: machine has nothing to drink, and the answer is that refusal --
        #: reached through every read the full answer would have gone through.
        with pytest.raises(power.Unpowered):
            await _craft_most({"identity_id": identity.id}, db, {"output": "silicon"})


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
