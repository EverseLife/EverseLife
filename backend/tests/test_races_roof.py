# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over one roof.

One of the race files (see `test_races.py` for the family's method): here the
contested thing is the stability of a working -- a number that belongs to the
vein and is shared by everyone digging it (D-188, D-099), so a swing and a
support of two different bodies write the same row. It is on the same list as
money and remainders, and raced the same way: two picks sagging one roof, two
last swings arriving at one cave-in, two supports set in one second.

What two picks do to the **ore** is a race about the remainder and lives in
`test_races_mining.py`; a face against what closes it from outside -- a death,
the moving ground -- is a race about the place and lives in
`test_races_face.py`.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from src.constants import current
from src.constants import registry as R
from src.engine import world
from src.models.identity import Body
from src.units import amount_float

ORE = "iron_ore"


async def test_two_bodies_swinging_one_vein_shake_one_roof(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The roof is the working's, and two miners sag it together (D-188, D-099).

    Two **different** bodies at one vein -- the ordinary case, since `start`
    refuses only a second face of the same body and `crowd_factor` exists to
    count the neighbours. They share no row but the vein's, and that is the
    one they must share: each swing has to read the roof the other left.

    The session used to carry a copy of it, taken at `start` and written back
    on every swing, so the second of these two put its own start minus one
    swing over the first's answer: one of the two sags was erased, and both
    miners were told a sign for a roof that was not there. The pause is before
    the vein's lock, so the two provably arrive at it together and the loser's
    read happens after the winner's commit.
    """
    from src.engine import frost, mining
    from src.models.mining import MiningSession, Pace
    from src.models.world import Vein

    #: Asked after the body is locked and before the vein is -- the one place
    #: to stand if both arms are to reach the vein's lock at once.
    _slow(monkeypatch, frost, "drain_multiplier")
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.artel.{stamp}", "Забой", area_m2=500)
    vein = await world.create_vein(session, node, ORE, richness=60, remaining=100_000)
    #: A working already shaken, so the assertion is about these two swings and
    #: not about the value an untouched vein is worth.
    started = constants[R.MINE_ROOF_TIMBER_CAP]
    vein.roof = Decimal(str(started))
    faces = []
    for who in ("Старший", "Подручный"):
        person = await world.create_identity(session, f"{who}-{stamp}")
        body = await world.print_body(session, person, node)
        await world.grant_item(
            session, await world.body_container(session, body), "stone_pickaxe", origin="тест"
        )
        face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY)
        session.add(face)
        faces.append(face)
    await session.flush()
    vein_id = vein.id
    face_ids = [face.id for face in faces]
    await session.commit()

    async def swing(face_id) -> None:
        async with factory() as db, db.begin():
            own = await db.get(MiningSession, face_id)
            assert own is not None
            await mining.swing(db, current(), own)

    outcome = await asyncio.gather(*(swing(one) for one in face_ids), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        shaken = await db.get(Vein, vein_id)
        assert shaken is not None
        expected = started - 2 * constants[R.MINE_ROOF_PER_SWING]
        assert float(shaken.roof) == pytest.approx(expected), (
            f"два удара просадили свод на один: {float(shaken.roof)} вместо {expected}"
        )


async def test_two_last_swings_at_once_cost_one_cave_in(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cave-in is counted on the body, and the count decides a death (D-294).

    Two sockets of one identity send the **last** swing in the same second.
    Without the lock on the face's own row both read it ACTIVE with the same
    roof, both take that roof to nought from their own stale copy, and the body
    lives through two cave-ins in one swing -- the second of which kills it.
    One swing, one cave-in, and the loser is told the face is closed.
    """
    from src.engine import mining
    from src.engine.mining import face as mining_face
    from src.models.identity import BodyState
    from src.models.mining import MiningSession, Pace

    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.last.{stamp}", "Забой", area_m2=100)
    vein = await world.create_vein(session, node, ORE, richness=60, remaining=100_000)
    who = await world.create_identity(session, f"Шахтёр-{stamp}")
    body = await world.print_body(session, who, node)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "stone_pickaxe", quality=50, origin="тест")
    #: One swing from nought: both arrive at the collapse or neither does. The
    #: roof sits on the vein, which is where the working keeps it (D-188).
    vein.roof = Decimal("1")
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY)
    session.add(face)
    await session.flush()
    body_id, face_id = body.id, face.id
    await session.commit()

    #: The pause goes after the face is read and before anything is written.
    _slow(monkeypatch, mining_face, "session_container")

    async def swing() -> None:
        async with factory() as db, db.begin():
            own = await db.get(MiningSession, face_id)
            assert own is not None
            await mining.swing(db, current(), own)

    outcomes = await asyncio.gather(swing(), swing(), return_exceptions=True)
    closed = [it for it in outcomes if isinstance(it, mining.SessionClosed)]
    assert len(closed) == 1, "второй удар обязан застать забой закрытым"

    async with factory() as db:
        again = await db.get(Body, body_id)
        assert again.cave_ins == 1, f"один обвал засчитан {again.cave_ins} раза"
        assert again.state is BodyState.ALIVE, "первый обвал щадит, и он здесь один"


async def test_the_support_that_arrived_at_the_ceiling_keeps_its_timber(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One support was enough, and the second must not spend itself proving it.

    The working is one support below the ceiling, and two sockets set one in
    the same second. The winner raises it to the ceiling; the loser wakes at a
    roof a support can no longer raise and is refused (`RoofHolds`) with its
    timber still in the pocket. Read from a stale copy, the loser would have
    computed the same rise from the same start, spent its timber and written
    the ceiling again -- one support out of two lost, and since D-294 a lost
    support is the difference between a body that lives through the next
    cave-in and one that does not.

    The pause holds the locks: `body_container` is the first thing asked
    after them.
    """
    from src.engine import mining
    from src.engine.mining import face as mining_face
    from src.models.inventory import Item
    from src.models.mining import MiningSession, Pace
    from src.models.world import Vein

    _slow(monkeypatch, mining_face, "body_container", 0.25)
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.cap.{stamp}", "Забой", area_m2=500)
    vein = await world.create_vein(session, node, ORE, richness=60, remaining=100_000)
    who = await world.create_identity(session, f"Крепильщик-{stamp}")
    body = await world.print_body(session, who, node)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "shaft_support", amount=2, origin="тест")
    #: Exactly one support below the ceiling, out of the constants: a playtest
    #: moving `mine.roof_per_timber` must not redden a test about locks.
    cap = constants[R.MINE_ROOF_TIMBER_CAP]
    vein.roof = Decimal(str(max(1.0, cap - constants[R.MINE_ROOF_PER_TIMBER])))
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY)
    session.add(face)
    await session.flush()
    pocket_id, face_id, vein_id = pocket.id, face.id, vein.id
    await session.commit()

    async def props() -> None:
        async with factory() as db, db.begin():
            own = await db.get(MiningSession, face_id)
            assert own is not None
            await mining.timber(db, current(), own)

    outcome = await asyncio.gather(props(), props(), return_exceptions=True)
    refused = [one for one in outcome if isinstance(one, mining.RoofHolds)]
    assert len(refused) == 1, f"вторая крепь обязана застать свод на потолке: {outcome}"
    assert not [one for one in outcome if isinstance(one, BaseException) and one not in refused]

    async with factory() as db:
        propped = await db.get(Vein, vein_id)
        assert propped is not None
        assert float(propped.roof) == pytest.approx(cap)
        again = await db.get(MiningSession, face_id)
        assert again is not None and again.timbers == 1, f"стоек {again.timbers}, а поднимала одна"
        left = await db.scalar(
            select(Item).where(Item.container_id == pocket_id, Item.type_key == "shaft_support")
        )
        assert left is not None, "отказ съел бревно"
        assert amount_float(left.amount) == 1, (
            f"в кармане {amount_float(left.amount)} вместо одного"
        )


async def test_two_supports_set_at_once_are_two_supports(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A support is a write to the roof, and the roof is a remainder.

    Two sockets of one identity set a support in the same second. Without the
    locks both read the same roof and the same stack of timber, and both write
    their own answer back: one of the two supports is free, and the roof rises
    once for two timbers -- or, with the stack read stale, one timber pays for
    both. After D-294 a lost support is the difference between a body that
    lives through the next cave-in and one that does not.

    What this pins is the vein's lock and the reread behind it: the loser
    queues at the vein, and the roof it then raises is the one the winner
    left. The timber's own lock is defence in depth and is **not** pinned
    here -- take it off and this stays green, because the vein has already
    serialised the two. It is there because a remainder guarded by somebody
    else's lock is guarded by a coincidence, and because `stack_up`, a trade
    or a workbench reach that stack without touching the vein at all.

    The pause holds the locks: `body_container` is the first thing asked
    after them.
    """
    from src.engine import mining
    from src.engine.mining import face as mining_face
    from src.models.inventory import Item
    from src.models.mining import MiningSession, Pace
    from src.models.world import Vein

    _slow(monkeypatch, mining_face, "body_container", 0.25)
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.prop.{stamp}", "Забой", area_m2=500)
    vein = await world.create_vein(session, node, ORE, richness=60, remaining=100_000)
    who = await world.create_identity(session, f"Крепильщик-{stamp}")
    body = await world.print_body(session, who, node)
    pocket = await world.body_container(session, body)
    #: Exactly two, so a stack read twice from the same value leaves one
    #: standing where none should.
    await world.grant_item(session, pocket, "shaft_support", amount=2, origin="тест")
    #: Low enough that both supports raise the roof, at today's numbers and
    #: at any retuning of them: two by `mine.roof_per_timber` below the
    #: ceiling. Written out of the constants rather than as a number, or a
    #: playtest moving `mine.roof_per_timber` reddens a test about locks.
    per_timber = constants[R.MINE_ROOF_PER_TIMBER]
    start = max(1.0, constants[R.MINE_ROOF_TIMBER_CAP] - 2 * per_timber)
    vein.roof = Decimal(str(start))
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY)
    session.add(face)
    await session.flush()
    pocket_id, face_id, vein_id = pocket.id, face.id, vein.id
    await session.commit()

    async def props() -> None:
        async with factory() as db, db.begin():
            own = await db.get(MiningSession, face_id)
            assert own is not None
            await mining.timber(db, current(), own)

    outcome = await asyncio.gather(props(), props(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        again = await db.get(MiningSession, face_id)
        assert again is not None
        assert again.timbers == 2, f"две стойки — две, а не {again.timbers}"
        propped = await db.get(Vein, vein_id)
        assert propped is not None
        raised = min(constants[R.MINE_ROOF_TIMBER_CAP], start + 2 * per_timber)
        assert float(propped.roof) == pytest.approx(raised), (
            f"свод поднят на одну стойку из двух: {float(propped.roof)} вместо {raised}"
        )
        left = await db.scalar(
            select(Item).where(Item.container_id == pocket_id, Item.type_key == "shaft_support")
        )
        assert left is None, "две стойки поставлены из одного бревна"
