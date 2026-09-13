# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once on the same diggings.

One of the race files (see `test_races.py` for the family's method): here the
contested thing is what the ground gives up and what the hands do with it --
a vein two picks swing at, a body two sockets spend, a face one socket works
while another walks out of it. Remainders of matter, raced the same way
money is. The drilling rig's hopper and coal are `test_races_rig.py`'s.

The **roof** of a working is contended the same way and by the same hands, but
it is a different number in a different row, and enough of it to fill a file:
it lives in `test_races_roof.py`. A face against what closes it from outside
-- a death, the moving ground -- is a race about the place and lives in
`test_races_face.py`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from src.constants import current
from src.constants import registry as R
from src.engine import world
from src.models.identity import Body
from src.units import ROUND_ROOF, amount_float, step

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
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY)
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


async def test_a_swing_that_waited_out_the_last_of_the_vein_pays_for_nothing(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The vein is worked out while a swing is on its way to the lock.

    Two picks share the remainder, and the rig shares it with both
    (`engine.rig`). A swing reads "there is rock left" before it queues at the
    vein's lock, and the last of that rock can be gone by the time it gets
    there -- so the check has to be taken again, on the locked row.

    Taken only once, the swing goes on with `min(per_swing, 0)` and tries to
    lay down a heap of nothing. `item.amount_positive` stops it, so the socket
    is answered with an IntegrityError -- an internal error where the vault
    keeps a word for a worked-out vein (`mining-vein-depleted`, pillar P2).
    Nothing is charged for the turn, because the transaction rolls back; that
    is also why the assertions on stamina, roof and swings below hold either
    way. What tells the two apart is the refusal: the world must answer with
    its own word and not with a crash.

    The roof is counted on the vein, where the working keeps it (D-188), so
    what is asserted is that it carries **one** swing's sag: the swing that
    took the last of the rock. The empty one adds nothing to it.
    """
    from src.engine import frost, mining
    from src.models.mining import MiningSession
    from src.models.world import Vein

    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.last.{stamp}", "Забой", area_m2=100)
    #: One unit left in the ground and some three and a half to a swing: the
    #: first pick to reach the lock takes the lot, whatever the vault's numbers
    #: are, and the second finds bare rock.
    vein = await world.create_vein(session, node, ORE, richness=60, remaining=1)
    faces = []
    for i in range(2):
        identity = await world.create_identity(session, f"Шахтёр-{i}-{stamp}")
        body = await world.print_body(session, identity, node)
        pocket = await world.body_container(session, body)
        await world.grant_item(session, pocket, "stone_pickaxe", quality=50, origin="тест")
        faces.append(await mining.start(session, current(), body, vein))
    late, first = faces
    late_id, first_id, late_body_id = late.id, first.id, late.body_id
    whole = mining.roof_of(current(), vein)
    stamina = float((await session.get(Body, late_body_id)).stamina)
    await session.commit()

    #: **A handshake, not a pause**, and patched only after the two sessions
    #: are open: the multiplier is asked by `start` as well, and it is the
    #: swing's asking that this waits on. The first caller is the late swing by
    #: construction -- the other arm does not begin until it has signalled.
    between_the_locks = asyncio.Event()
    took_the_last = asyncio.Event()
    asking = frost.drain_multiplier
    waiting = True

    async def held(*args, **kwargs):
        nonlocal waiting
        chill = await asking(*args, **kwargs)
        if waiting:
            waiting = False
            between_the_locks.set()
            #: Timed out rather than waited on for ever: the two sides wait for
            #: each other, and a failure before a `set()` would hang the run
            #: instead of failing it.
            await asyncio.wait_for(took_the_last.wait(), timeout=5)
        return chill

    monkeypatch.setattr(frost, "drain_multiplier", held)
    refused: list[BaseException] = []

    async def swings_late() -> None:
        try:
            async with factory() as db, db.begin():
                own = await db.get(MiningSession, late_id)
                assert own is not None
                await mining.swing(db, current(), own)
        except mining.VeinDepleted as refusal:
            refused.append(refusal)

    async def takes_the_last() -> None:
        #: The late swing is past the body and not yet at the vein -- by
        #: construction.
        await asyncio.wait_for(between_the_locks.wait(), timeout=5)
        async with factory() as db, db.begin():
            own = await db.get(MiningSession, first_id)
            assert own is not None
            await mining.swing(db, current(), own)
        took_the_last.set()

    outcome = await asyncio.gather(swings_late(), takes_the_last(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        assert await db.scalar(select(Vein.remaining).where(Vein.id == vein.id)) == 0
        late_again = await db.get(MiningSession, late_id)
        assert late_again is not None
        assert late_again.swings == 0, "удар по пустой жиле засчитан"
        sagged = await db.get(Vein, vein.id)
        assert sagged is not None
        #: To the hundredth the column keeps: the starting roof is a working's
        #: own since D-302 and does not land on that grid by itself.
        one_swing = whole - constants[R.MINE_ROOF_PER_SWING]
        assert float(sagged.roof) == pytest.approx(one_swing, abs=float(step(ROUND_ROOF))), (
            "свод просел от удара, который ничего не добыл"
        )
        body = await db.get(Body, late_body_id)
        assert body is not None
        assert float(body.stamina) == stamina, "выносливость списана за пустой удар"
        assert not await world.contents(db, await mining.session_container(db, late_again))
        assert refused, "удар по выработанной жиле прошёл молча"


async def test_a_swing_and_a_leave_of_one_face_do_not_strand_the_ore(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third closer, and the only one that shares no lock with a swing.

    A death takes the body FOR UPDATE before it reaches the face, and the
    eruption meets a swing on the vein. `leave` does neither: it takes the
    session row and nothing else. So a `leave` that reads the face's things
    before a swing lays its ore down, and writes LEFT after, walks off with the
    old haul and leaves the new ore in a container `leave` itself will refuse
    to open ever again -- refused by state, and there is no other door.

    Nothing weaker than a lock closes that: a reread narrows the window and
    does not shut it, because the two never queue anywhere. So the swing takes
    the session row **after** the vein -- the eruption's own direction, which
    closes no circle -- and the leave waits at it, then carries out everything,
    the swing's ore included.

    Without that lock the pair does not merely lose the ore here, it crosses:
    `stack_up` takes the twins in the face's container under a lock, so the
    swing holds the old heap and waits for the session row, while the leave
    holds the session row and waits for that heap to carry it out -- ABBA, and
    the database kills one of the two. The quiet loss is the same defect on an
    empty face, where there is no heap to contend and nothing to collide with.

    **The handshake has to land in the right window**, and it is a narrow one:
    between the flush that inserts the ore and the flush that writes the
    session row. `remember_roof`, a line later, is already too late -- that
    second flush takes the row by writing it, so a leave arriving then queues
    anyway and the test passes with no lock at all. So the pause sits in
    `stack_up`, the flush that lays the ore down, and only on its first call:
    the leave folds heaps of its own, and slowing those proves nothing.
    """
    from src.engine import mining
    from src.models.mining import MiningSession, SessionState
    from src.models.world import Vein

    laid_the_ore = asyncio.Event()
    folding = world.stack_up

    async def held(*args, **kwargs):
        heap = await folding(*args, **kwargs)
        if not laid_the_ore.is_set():
            laid_the_ore.set()
            await asyncio.sleep(0.25)
        return heap

    monkeypatch.setattr(world, "stack_up", held)
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.face.{stamp}", "Забой", area_m2=500)
    vein = await world.create_vein(session, node, ORE, richness=60, remaining=100_000)
    who = await world.create_identity(session, f"Шахтёр-{stamp}")
    body = await world.print_body(session, who, node)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "stone_pickaxe", quality=50, origin="тест")
    face = await mining.start(session, current(), body, vein)
    #: A haul already at the face: what the leave carries out must be this and
    #: the swing's, and the two are told apart by the vein's own bookkeeping.
    await world.grant_item(
        session,
        await mining.session_container(session, face),
        ORE,
        amount=9,
        quality=60,
        origin="тест",
    )
    body_id, vein_id, face_id = body.id, vein.id, face.id
    rock_was = vein.remaining
    await session.commit()

    began_the_leave = asyncio.Event()

    async def swings() -> None:
        async with factory() as db, db.begin():
            own = await db.get(MiningSession, face_id)
            assert own is not None
            await mining.swing(db, current(), own)
        #: The two must actually have met. A leave that never reached the row
        #: -- too slow to open a connection, say -- leaves every assertion
        #: below true and proves none of them, and the family has no other
        #: guard against a race that did not happen.
        assert began_the_leave.is_set(), "уход не успел в окно: гонки не было"

    async def leaves() -> None:
        #: The swing has laid its ore down and not yet written the session
        #: row -- by construction.
        await asyncio.wait_for(laid_the_ore.wait(), timeout=5)
        async with factory() as db, db.begin():
            own = await db.get(MiningSession, face_id)
            assert own is not None
            began_the_leave.set()
            await mining.leave(db, current(), own)

    outcome = await asyncio.gather(swings(), leaves(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        closed = await db.get(MiningSession, face_id)
        assert closed is not None and closed.state is SessionState.LEFT
        assert closed.swings == 1, "удар по открытому забою не засчитан"
        stuck = await world.contents(db, await mining.session_container(db, closed))
        assert not stuck, "руда осталась в контейнере закрытой сессии"
        mined = amount_float(
            rock_was - await db.scalar(select(Vein.remaining).where(Vein.id == vein_id))
        )
        carried = sum(
            amount_float(thing.amount)
            for thing in await world.contents(
                db, await world.body_container(db, await db.get(Body, body_id))
            )
            if thing.type_key == ORE
        )
        assert carried == pytest.approx(9 + mined), (
            f"вынесено {carried}, а добыто и лежало {9 + mined}"
        )


async def test_a_leave_that_won_the_face_refuses_the_swing_behind_it(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same pair with the gate the other way round.

    The leave holds the session row, so the swing queues at it and finds the
    face closed when it gets there. It must refuse rather than work a face
    somebody has walked out of -- and refuse having written nothing: the vein
    keeps its remainder, the body its strength.

    The handshake rides on `session_container`, the first thing `leave` asks
    after taking the row.
    """
    from src.engine import mining
    from src.engine.mining import face as mining_face
    from src.models.mining import MiningSession, SessionState
    from src.models.world import Vein

    holds_the_face = asyncio.Event()
    asking = mining_face.session_container

    async def held(*args, **kwargs):
        container = await asking(*args, **kwargs)
        if not holds_the_face.is_set():
            holds_the_face.set()
            await asyncio.sleep(0.25)
        return container

    monkeypatch.setattr(mining_face, "session_container", held)
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.face.{stamp}", "Забой", area_m2=500)
    vein = await world.create_vein(session, node, ORE, richness=60, remaining=100_000)
    who = await world.create_identity(session, f"Шахтёр-{stamp}")
    body = await world.print_body(session, who, node)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "stone_pickaxe", quality=50, origin="тест")
    face = await mining.start(session, current(), body, vein)
    await world.grant_item(
        session,
        await mining.session_container(session, face),
        ORE,
        amount=9,
        quality=60,
        origin="тест",
    )
    body_id, vein_id, face_id = body.id, vein.id, face.id
    rock_was, stamina = vein.remaining, float(body.stamina)
    await session.commit()

    refused: list[BaseException] = []

    async def swings() -> None:
        #: The leave is past the gate and holding it -- by construction.
        await asyncio.wait_for(holds_the_face.wait(), timeout=5)
        try:
            async with factory() as db, db.begin():
                own = await db.get(MiningSession, face_id)
                assert own is not None
                await mining.swing(db, current(), own)
        except mining.SessionClosed as refusal:
            refused.append(refusal)

    async def leaves() -> None:
        async with factory() as db, db.begin():
            own = await db.get(MiningSession, face_id)
            assert own is not None
            await mining.leave(db, current(), own)

    outcome = await asyncio.gather(swings(), leaves(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        closed = await db.get(MiningSession, face_id)
        assert closed is not None and closed.state is SessionState.LEFT
        assert closed.swings == 0, "удар по закрытому забою засчитан"
        assert await db.scalar(select(Vein.remaining).where(Vein.id == vein_id)) == rock_was
        mine = await db.get(Body, body_id)
        assert mine is not None and float(mine.stamina) == stamina
        assert refused, "удар по покинутому забою прошёл молча"
