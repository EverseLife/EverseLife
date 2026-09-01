# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once on the same diggings.

One of the race files (see `test_races.py` for the family's method): here the
contested thing is what the ground gives up -- a vein two picks swing at, a
body two sockets spend, a coal heap the tick burns while a hand carries it,
an oil hopper two carters empty. Remainders of matter, raced the same way
money is.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from src.constants import current
from src.engine import stock, world
from src.models.identity import Body
from src.models.inventory import Item

ORE = "iron_ore"


async def test_two_swings_at_once_are_paid_for_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stamina is on the same list as money and remainders (CLAUDE.md).

    Two sockets of one identity, two swings in the same second. Without the
    lock on the body both read the same reserve, both find it enough, and both
    write their own remainder -- the second write erases the first, and one of
    the swings is free. The ore, meanwhile, is mined twice: the vein is locked,
    so it is honestly spent.
    """
    from src.engine import frost, mining
    from src.models.mining import MiningSession, Pace

    #: The pause goes between the reading of the reserve and its write-off:
    #: `drain_multiplier` is the last thing asked before the price is computed.
    _slow(monkeypatch, frost, "drain_multiplier")
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.face.{stamp}", "Забой", area_m2=500)
    vein = await world.create_vein(session, node, ORE, richness=70, remaining=100_000)
    who = await world.create_identity(session, f"Шахтёр-{stamp}")
    body = await world.print_body(session, who, node)
    body.stamina = Decimal("90")
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=100)
    session.add(face)
    await session.flush()
    body_id, face_id = body.id, face.id
    was = float(body.stamina)
    await session.commit()

    async def swing() -> None:
        async with factory() as db, db.begin():
            open_face = await db.get(MiningSession, face_id)
            assert open_face is not None
            await mining.swing(db, current(), open_face)

    await asyncio.gather(swing(), swing(), return_exceptions=True)

    async with factory() as db:
        again = await db.get(Body, body_id)
        spent = was - float(again.stamina)
        one = mining.swing_cost(current(), again, Pace.STEADY, datetime.now(UTC), chill=1.0)
        assert spent == pytest.approx(2 * one, rel=0.05), (
            f"два удара списали {spent:.2f} вместо {2 * one:.2f}: один достался бесплатно"
        )


async def test_two_swings_on_one_vein_do_not_mine_the_same_ore_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The vein is shared; without its lock two miners read the same
    remainder and both subtract from it -- ore out of thin air."""
    from src.engine import mining
    from src.models.mining import MiningSession
    from src.models.world import Vein

    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.vein.{stamp}", "Забой", area_m2=100)
    vein = await world.create_vein(session, node, ORE, richness=60, remaining=100_000)
    sessions = []
    for i in range(2):
        identity = await world.create_identity(session, f"Шахтёр-{i}-{stamp}")
        body = await world.print_body(session, identity, node)
        pocket = await world.body_container(session, body)
        await world.grant_item(session, pocket, "stone_pickaxe", quality=50, origin="тест")
        sessions.append((await mining.start(session, current(), body, vein)).id)
    await session.commit()
    start = await session.scalar(select(Vein.remaining).where(Vein.id == vein.id))
    #: Patched where the name is looked up (D-252 split): `face` binds
    #: `session_container` into its own globals, so slowing the package
    #: re-export would slow nobody.
    from src.engine.mining import face as mining_face

    _slow(monkeypatch, mining_face, "session_container")

    async def swing(session_id: uuid.UUID) -> float:
        async with factory() as db, db.begin():
            own = await db.get(MiningSession, session_id)
            return float((await mining.swing(db, current(), own)).mined)

    mined = await asyncio.gather(*(swing(s) for s in sessions))
    left = await session.scalar(select(Vein.remaining).where(Vein.id == vein.id))
    from src.units import amount as to_units

    assert start - left == sum(to_units(m) for m in mined), (
        "жила отдала ровно столько, сколько добыто"
    )


async def test_burning_coal_and_carrying_it_away_at_once_keep_the_count(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared yard: the tick burns coal while a player picks it up. Both
    read the stack, both write it -- without the lock one write is lost and
    coal is either doubled or vanishes (wave 2, item 4a)."""
    from src.engine import rig

    stamp = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.yard.{stamp}", "Двор", area_m2=100)
    yard = await world.node_container(session, node)
    coal = await world.grant_item(session, yard, "coal", amount=10, origin="тест")
    identity = await world.create_identity(session, f"Носильщик-{stamp}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    await session.commit()
    #: The tick counts the coal, and the carry commits before the tick locks:
    #: the stack the tick already holds in memory is stale by then.
    _slow(monkeypatch, rig, "_coal_available")

    async def burn() -> None:
        async with factory() as db, db.begin():
            #: As the tick does: count the coal first, then burn it. The count
            #: loads the stack into the session before the lock; the lock must
            #: reread it, or the burn writes from the value before the carry.
            assert await rig._coal_available(db, yard.id) >= 4
            await rig._burn(db, yard.id, 4)

    async def carry() -> None:
        async with factory() as db, db.begin():
            own = await db.get(Item, coal.id)
            target = await db.get(type(pocket), pocket.id)
            await world.move_stack(db, own, target, 3)

    await asyncio.gather(burn(), carry())
    rows = (
        await session.execute(select(Item.container_id, Item.amount).where(Item.type_key == "coal"))
    ).all()
    from src.units import amount as to_units

    assert sum(a for _, a in rows) == to_units(10 - 4), "сгорело четыре, унесено три, всего шесть"
    assert dict(rows)[pocket.id] == to_units(3)


async def test_locked_stacks_reread_what_the_session_already_holds(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    """A stack loaded before the lock -- the tick counts the coal before it
    burns it -- is reread by the lock: without `populate_existing` the lock
    is on the fresh row and the write comes from the stale object."""
    from sqlalchemy import update

    node = await world.create_node(
        session, f"terra.stale.{uuid.uuid4().hex[:6]}", "Двор", area_m2=1
    )
    yard = await world.node_container(session, node)
    coal = await world.grant_item(session, yard, "coal", amount=10, origin="тест")
    await session.commit()

    async with factory() as db, db.begin():
        held = (await db.execute(select(Item).where(Item.id == coal.id))).scalar_one()
        assert held.amount == 10_000
        async with factory() as other, other.begin():
            await other.execute(update(Item).where(Item.id == coal.id).values(amount=7_000))
        locked = await stock.locked_stacks(db, yard.id, ("coal",))
        assert locked[0] is held and held.amount == 7_000, "замок обязан перечитать строку"


async def test_two_empties_of_one_liquid_hopper_pour_each_unit_once(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The oil hopper is money-shaped (D-252): two carters emptying it at once
    must not pour the same units into two canisters. The rig row is taken
    `with_for_update`, so the second empties what the first left."""
    from src.engine import rig, storage

    stamp = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.oil.{stamp}", "Поле", area_m2=200)
    vein = await world.create_vein(session, node, "crude_oil", richness=55, remaining=100_000)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "coal", amount=100, quality=55, origin="тест")
    #: Two canisters standing in the node: together they hold more than the
    #: hopper gave, so every pumped unit has somewhere to go.
    vessels = [
        await world.grant_item(session, yard, "canister", quality=60, origin="тест")
        for _ in range(2)
    ]
    identity = await world.create_identity(session, f"Нефтяник-{stamp}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    machine = await world.grant_item(session, pocket, "drilling_rig", quality=70, origin="тест")
    installation = await rig.place(session, body, machine, vein)
    #: The hopper is filled and pinned to one moment: both empties advance to
    #: the same "now", so time adds nothing between them.
    moment = installation.counted_at + timedelta(hours=6)
    pumped = await rig.advance(session, current(), installation, now=moment)
    await session.commit()
    _slow(monkeypatch, rig, "advance")

    async def take() -> float:
        async with factory() as db, db.begin():
            own_body = await db.get(Body, body.id)
            own_rig = await db.get(type(installation), installation.id)
            with contextlib.suppress(rig.NoRoom):
                return await rig.empty_hopper(db, current(), own_body, own_rig, now=moment)
            return 0.0

    taken = await asyncio.gather(take(), take())

    from src.units import amount as to_units

    poured = 0
    for vessel in vessels:
        inside = await storage.inside(session, await session.get(Item, vessel.id))
        rows = (
            (await session.execute(select(Item).where(Item.container_id == inside.id)))
            .scalars()
            .all()
        )
        poured += sum(int(r.amount) for r in rows)
    left = await session.scalar(
        select(type(installation).hopper).where(type(installation).id == installation.id)
    )
    assert poured + to_units(float(left)) == to_units(pumped), (
        "каждая единица нефти налита ровно один раз: бункер плюс тара сходятся с добытым"
    )
    assert poured == to_units(sum(taken)), "слито ровно столько, сколько отдано вызовами"
