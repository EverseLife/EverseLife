# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once on one working face.

One of the race files (see `test_races.py` for the family's method): here the
contested thing is the working itself -- the session row every closer of a
face takes as its gate, the haul lying in its container, and the roof the vein
under it remembers (D-188).

A face has one way to end from the inside and three from the outside, and the
three are what fills this file: a death (`mining.abandon`), the ground
carrying the vein away (`plates._close_faces`), and a `leave` sent from a
second socket of the same identity. The lock order they all agree on is
`body -> vein -> session`, and the eruption goes `veins -> sessions` for the
same reason -- reverse either and the database kills one of the two.

Cut out of `test_races_ground.py` when it outgrew the length the quality bar
allows one file.
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
from src.engine import travel, world
from src.models.identity import Body
from src.models.world import Node, Surface, Vein
from src.units import amount_float

ORE = "iron_ore"


async def test_death_and_leaving_one_face_do_not_both_carry_the_haul(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One face, two ways out of it, and one haul.

    A rift takes the miner while the ground moves the vein: `death.die` closes
    the face and leaves the ore in the node, `plates._close_faces` closes it
    and carries the ore into a pocket. Both are right on their own. Together
    they are two transactions over one haul, and the session row is the gate
    both take **first**: whoever wins it plays the whole story out while the
    loser waits at it holding nothing the winner could want.

    What goes red without the gate is the **order**: one side holds the pocket
    and waits for the session, the other holds the session and waits for the
    pocket (`leave` carries the haul into it), and the database kills one of
    them. So the assertion that actually catches it is the one about neither
    call raising -- the count of the ore is the invariant that must hold
    afterwards, not the thing the lock buys.
    """
    from src.engine import death, mining, plates
    from src.engine.mining import face as mining_face
    from src.models.mining import MiningSession, Pace, SessionState
    from src.models.world import Layer, Planet

    #: **A handshake, not a pause**, like the carry-out tests above: the
    #: eruption must arrive while the death holds the gate -- by construction,
    #: not after a guessed number of milliseconds. A cold connection costs
    #: some seventy milliseconds to open, so a guessed head start loses often
    #: enough for the eruption to win the gate, carry the haul into the pocket
    #: and hand it to the salvage -- a legal outcome, but not the branch this
    #: test pins. `session_container` is the first thing `abandon` asks after
    #: taking the session row, and `face` binds it into its own globals
    #: (D-252 split), so the patch lands where the closers look it up while
    #: the fixture's call below goes through the package door to the original.
    holds_the_gate = asyncio.Event()
    asking = mining_face.session_container

    async def held(*args, **kwargs):
        container = await asking(*args, **kwargs)
        holds_the_gate.set()
        await asyncio.sleep(0.2)
        return container

    monkeypatch.setattr(mining_face, "session_container", held)
    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session, "pyroxis", "Пироксис", planet=Planet.PYROXIS, area_m2=1, layer=Layer.SPACE
    )
    field = await world.create_node(
        session,
        f"pyroxis.{stamp}.field",
        "Чёрное поле",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
        parent=sphere,
    )
    near = await world.create_node(
        session,
        f"pyroxis.{stamp}.near",
        "Соседнее поле",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
        parent=sphere,
    )
    await travel.connect(session, field, near, base_seconds=900, surface=Surface.TRAIL)
    vein = await world.create_vein(session, field, ORE, richness=70, remaining=1000)
    who = await world.create_identity(session, f"Вахтовик-{stamp}")
    body = await world.print_body(session, who, field)
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=100)
    session.add(face)
    await session.flush()
    await world.grant_item(
        session,
        await mining.session_container(session, face),
        ORE,
        amount=9,
        quality=60,
        origin="тест",
    )
    #: And the long branch of `die`: a pocket with something in it, and a heap
    #: of the same goods already lying in the node. Then the death, past the
    #: gate, locks the pocket and lays its salvage into that heap --
    #: `stack_up` takes the node's things under a lock -- the very rows the
    #: eruption's `leave` would carry the haul into. With an empty pocket the
    #: death skips all of that and the test walks a branch where the order
    #: cannot be wrong.
    await world.grant_item(
        session,
        await world.body_container(session, body),
        ORE,
        amount=4,
        quality=60,
        origin="тест",
    )
    await world.grant_item(
        session,
        await world.node_container(session, field),
        ORE,
        amount=2,
        quality=60,
        origin="тест",
    )
    field_id, body_id, vein_id, face_id = field.id, body.id, vein.id, face.id
    await session.commit()

    async def dies() -> None:
        async with factory() as db, db.begin():
            mine = await db.get(Body, body_id)
            assert mine is not None
            await death.die(db, current(), mine, cause="rift")

    async def ground_moves() -> None:
        #: The death is past its gate and inside the face -- not probably,
        #: but by construction.
        await holds_the_gate.wait()
        async with factory() as db, db.begin():
            rock = await db.get(Vein, vein_id)
            assert rock is not None
            await plates._close_faces(db, current(), rock, now=datetime.now(UTC))

    outcome = await asyncio.gather(dies(), ground_moves(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        closed = await db.get(MiningSession, face_id)
        assert closed is not None and closed.state is SessionState.LEFT
        #: Nine units were mined, and nine units exist -- wherever they ended up.
        here = await world.contents(
            db, await world.node_container(db, await db.get(Node, field_id))
        )
        pocket = await world.contents(
            db, await world.body_container(db, await db.get(Body, body_id))
        )
        total = sum(
            float(thing.amount) / 1000 for thing in [*here, *pocket] if thing.type_key == ORE
        )
        #: Nine in the face, two lying in the node, and of the four in the
        #: pocket whatever the salvage roll kept -- never more than fifteen and
        #: never less than the eleven that were never on the body.
        assert 11 <= total <= 15, f"добытое размножилось или пропало: {total}"


async def test_death_and_the_burning_ground_close_one_face_without_a_deadlock(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second arm of the same ABBA: the fire against the death.

    `plates.erupted` burns the yards and only later closes the faces, while a
    death lays what the face and the pocket held into those same yards. In the
    old order -- the yards first, the session rows only inside `_close_faces` --
    the job held the burning heaps and waited for the gate the death held, and
    the death held its gate and waited for a heap the fire held. The pre-lock
    of the veins and the sessions **before the first flame** (mirrored here
    from `erupted`) makes the gate the meeting point: whoever comes second
    waits at it holding nothing the winner wants.

    The handshake rides on `stack_up`: the first heap the death lays into the
    yard comes after its gate, so the fire that waits for it arrives with the
    death provably past the gate and holding the heap -- by construction, not
    after a guessed number of milliseconds. On the old order of `die` the same
    handshake fired from the salvage, **before** the gate, and the test died
    of the very ABBA it now pins shut.
    """
    from src.engine import death, mining, plates
    from src.models.mining import MiningSession, Pace, SessionState
    from src.models.world import Layer, Planet

    laid_a_heap = asyncio.Event()
    laying = world.stack_up

    async def held(*args, **kwargs):
        heap = await laying(*args, **kwargs)
        laid_a_heap.set()
        await asyncio.sleep(0.2)
        return heap

    monkeypatch.setattr(world, "stack_up", held)
    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session, "pyroxis", "Пироксис", planet=Planet.PYROXIS, area_m2=1, layer=Layer.SPACE
    )
    field = await world.create_node(
        session,
        f"pyroxis.{stamp}.field",
        "Чёрное поле",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
        parent=sphere,
    )
    vein = await world.create_vein(session, field, ORE, richness=70, remaining=1000)
    who = await world.create_identity(session, f"Вахтовик-{stamp}")
    body = await world.print_body(session, who, field)
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=100)
    session.add(face)
    await session.flush()
    await world.grant_item(
        session,
        await mining.session_container(session, face),
        ORE,
        amount=9,
        quality=60,
        origin="тест",
    )
    #: A pocket with goods and a heap of the same ore in the yard: the death
    #: then lays both hauls into the field -- the twins `stack_up` locks are
    #: the very rows the fire takes first.
    await world.grant_item(
        session,
        await world.body_container(session, body),
        ORE,
        amount=4,
        quality=60,
        origin="тест",
    )
    await world.grant_item(
        session,
        await world.node_container(session, field),
        ORE,
        amount=2,
        quality=60,
        origin="тест",
    )
    field_id, body_id, vein_id, face_id = field.id, body.id, vein.id, face.id
    await session.commit()

    async def dies() -> None:
        async with factory() as db, db.begin():
            mine = await db.get(Body, body_id)
            assert mine is not None
            await death.die(db, current(), mine, cause="rift")

    async def ground_burns() -> None:
        #: The death is past its gate and laying heaps -- not probably, but
        #: by construction.
        await laid_a_heap.wait()
        async with factory() as db, db.begin():
            place = await db.get(Node, field_id)
            rock = await db.get(Vein, vein_id)
            assert place is not None and rock is not None
            #: The eruption's own order, mirrored from `plates.erupted`: the
            #: veins, then the sessions at the faces -- the gate -- and only
            #: then the fire and the closing.
            await db.execute(
                select(Vein).where(Vein.node_id == place.id).order_by(Vein.id).with_for_update()
            )
            await db.execute(
                select(MiningSession)
                .join(Vein, Vein.id == MiningSession.vein_id)
                .where(Vein.node_id == place.id, MiningSession.state == SessionState.ACTIVE)
                .order_by(MiningSession.id)
                .with_for_update(of=MiningSession)
            )
            await plates._burn(db, [place])
            await plates._close_faces(db, current(), rock, now=datetime.now(UTC))

    outcome = await asyncio.gather(dies(), ground_burns(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        closed = await db.get(MiningSession, face_id)
        assert closed is not None and closed.state is SessionState.LEFT
        here = await world.contents(
            db, await world.node_container(db, await db.get(Node, field_id))
        )
        pocket = await world.contents(
            db, await world.body_container(db, await db.get(Body, body_id))
        )
        total = sum(
            float(thing.amount) / 1000 for thing in [*here, *pocket] if thing.type_key == ORE
        )
        #: The death won the gate, so everything it laid down was lying under
        #: the open sky when the fire came -- and what lies in a shaken field
        #: burns with it, to the last unit (D-197).
        assert total == 0, f"уложенное в поле должно сгореть с полем: {total}"


async def test_a_swing_and_the_eruption_pass_each_other_without_a_deadlock(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third arm of the same ABBA: the pick against the fire's pre-lock.

    `plates.erupted` takes the veins of a shaken node and only then the session
    rows at their faces, and it says why in so many words: a swing holds its
    vein and then writes its session row, so taking them here the other way
    round would cross it. A swing that reached for the face's own row FOR
    UPDATE **before** the vein would close exactly that circle -- and it does:
    written that way, this test dies of `DeadlockDetectedError`.

    Hence the swing takes no lock on the face at all. Two sockets of one
    identity are serialised by the body's row, which they share and the
    eruption never wants (`mining.face.swing`), and the face is only reread
    there -- a plain read that waits for nobody.

    The handshake rides on the stamina multiplier, asked after the body is
    locked and before the vein is: the fire arrives with the swing provably
    inside that window, by construction rather than after a guessed number of
    milliseconds.
    """
    from src.engine import frost, mining
    from src.models.mining import MiningSession, Pace, SessionState
    from src.models.world import Layer, Planet

    between_the_locks = asyncio.Event()
    asking = frost.drain_multiplier

    async def held(*args, **kwargs):
        chill = await asking(*args, **kwargs)
        between_the_locks.set()
        await asyncio.sleep(0.2)
        return chill

    monkeypatch.setattr(frost, "drain_multiplier", held)
    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session, f"pyroxis.{stamp}", "Пироксис", planet=Planet.PYROXIS, area_m2=1, layer=Layer.SPACE
    )
    field = await world.create_node(
        session,
        f"pyroxis.{stamp}.field",
        "Чёрное поле",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
        parent=sphere,
    )
    vein = await world.create_vein(session, field, ORE, richness=70, remaining=100_000)
    who = await world.create_identity(session, f"Вахтовик-{stamp}")
    body = await world.print_body(session, who, field)
    await world.grant_item(
        session,
        await world.body_container(session, body),
        "stone_pickaxe",
        quality=50,
        origin="тест",
    )
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=100)
    session.add(face)
    await session.flush()
    field_id, face_id = field.id, face.id
    await session.commit()

    async def swings() -> None:
        async with factory() as db, db.begin():
            own = await db.get(MiningSession, face_id)
            assert own is not None
            await mining.swing(db, current(), own)

    async def ground_shakes() -> None:
        #: The swing is past the body and not yet at the vein -- by construction.
        await between_the_locks.wait()
        async with factory() as db, db.begin():
            #: The eruption's own order, mirrored from `plates.erupted`.
            await db.execute(
                select(Vein).where(Vein.node_id == field_id).order_by(Vein.id).with_for_update()
            )
            await db.execute(
                select(MiningSession)
                .join(Vein, Vein.id == MiningSession.vein_id)
                .where(Vein.node_id == field_id, MiningSession.state == SessionState.ACTIVE)
                .order_by(MiningSession.id)
                .with_for_update(of=MiningSession)
            )

    outcome = await asyncio.gather(swings(), ground_shakes(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome


async def test_a_swing_queued_at_the_vein_does_not_work_a_face_the_ground_took(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of that pass: not a deadlock, but the loser's own state.

    A swing checks the face is open and then queues at the vein's lock -- and
    that queue is exactly where an eruption fits, because it takes the veins of
    a shaken node before the sessions at them. The job that goes ahead closes
    this face through `leave` and lights the vein up in a neighbouring node, so
    the swing wakes holding a lock on rock that is no longer here, under a
    session that is no longer open.

    Without the reread after that wait the swing goes on from what it checked
    **before** it: the ore it mines is laid into the container of a closed
    session, where nothing can ever reach it again -- `leave` refuses by state
    -- and `remember_roof` stamps the sag onto a vein that has moved away, so
    the next miner in the neighbouring node meets a roof shaken by a swing
    struck in a field they have never been to.

    Both halves are asserted on the world rather than on the refusal: what the
    eruption carried out is nine units and not one more, and the vein carries
    the roof it had before the swing rather than the one that swing would have
    given it. The vein is set up **already shaken**, so the second assertion is
    about this swing and not about a NULL that happens to still be there --
    whether a vein ought to carry its sag into the node it moves to is a
    question for the vault, and this test must not answer it by accident.
    """
    from src.engine import frost, mining, plates
    from src.models.mining import MiningSession, Pace, SessionState
    from src.models.world import Layer, Planet

    #: **A handshake, not a pause.** The swing is let go only when the face is
    #: provably closed and the eruption a breath from its commit: then the vein
    #: it reaches for is held by that job, and the wait at the lock -- the
    #: window this test is about -- happens by construction rather than after a
    #: guessed number of milliseconds. The multiplier is asked after the body
    #: is locked and before the vein is, which is the one place to stand.
    between_the_locks = asyncio.Event()
    closed_the_face = asyncio.Event()
    asking = frost.drain_multiplier

    async def held(*args, **kwargs):
        chill = await asking(*args, **kwargs)
        between_the_locks.set()
        #: Each side of this handshake waits for the other, so a failure before
        #: a `set()` would hang the run rather than fail it -- the suite has no
        #: timeout of its own (`test_reads.py` guards a wait the same way).
        await asyncio.wait_for(closed_the_face.wait(), timeout=5)
        return chill

    monkeypatch.setattr(frost, "drain_multiplier", held)
    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session, f"pyroxis.{stamp}", "Пироксис", planet=Planet.PYROXIS, area_m2=1, layer=Layer.SPACE
    )
    field = await world.create_node(
        session,
        f"pyroxis.{stamp}.field",
        "Чёрное поле",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
        parent=sphere,
    )
    near = await world.create_node(
        session,
        f"pyroxis.{stamp}.near",
        "Соседнее поле",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
        parent=sphere,
    )
    vein = await world.create_vein(session, field, ORE, richness=70, remaining=100_000)
    #: A working somebody has already dug in: the sag is on the vein, where
    #: D-188 keeps it, so the swing that must not happen has a value to spoil.
    vein.roof = Decimal("80")
    who = await world.create_identity(session, f"Вахтовик-{stamp}")
    body = await world.print_body(session, who, field)
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=80)
    session.add(face)
    await session.flush()
    #: A haul already lying in the face, so the count afterwards tells the two
    #: apart: nine is what the eruption carried out, more than nine is a swing
    #: that went on into a closed session.
    await world.grant_item(
        session,
        await mining.session_container(session, face),
        ORE,
        amount=9,
        quality=60,
        origin="тест",
    )
    field_id, near_id, body_id, vein_id, face_id = field.id, near.id, body.id, vein.id, face.id
    stamina, rock_left, sag = float(body.stamina), vein.remaining, vein.roof
    await session.commit()

    #: The refusal is caught rather than awaited on, so that the assertions
    #: below run and the failure names the damage -- ore in a container nobody
    #: can open, a roof on rock in another node -- instead of the refusal that
    #: was meant to prevent it. Anything but `SessionClosed` goes out to the
    #: gather, where the first assertion catches it.
    refused: list[BaseException] = []

    async def swings() -> None:
        try:
            async with factory() as db, db.begin():
                own = await db.get(MiningSession, face_id)
                assert own is not None
                await mining.swing(db, current(), own)
        except mining.SessionClosed as refusal:
            refused.append(refusal)

    async def ground_moves() -> None:
        #: The swing is past the body and not yet at the vein -- by construction.
        await asyncio.wait_for(between_the_locks.wait(), timeout=5)
        async with factory() as db, db.begin():
            rock = await db.get(Vein, vein_id)
            assert rock is not None
            #: The eruption's own order, mirrored from `plates.erupted`: the
            #: veins of the shaken node, then the sessions at their faces.
            await db.execute(
                select(Vein).where(Vein.node_id == field_id).order_by(Vein.id).with_for_update()
            )
            await db.execute(
                select(MiningSession)
                .join(Vein, Vein.id == MiningSession.vein_id)
                .where(Vein.node_id == field_id, MiningSession.state == SessionState.ACTIVE)
                .order_by(MiningSession.id)
                .with_for_update(of=MiningSession)
            )
            await plates._close_faces(db, current(), rock, now=datetime.now(UTC))
            #: And the move itself, mirrored from `plates._move_veins`: the
            #: vein goes out here and lights up next door (D-197).
            rock.node_id = near_id
            closed_the_face.set()
            #: Long enough for the swing to reach the vein's lock and queue at
            #: it while this transaction still holds it.
            await asyncio.sleep(0.1)

    outcome = await asyncio.gather(swings(), ground_moves(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        closed = await db.get(MiningSession, face_id)
        assert closed is not None and closed.state is SessionState.LEFT
        rock = await db.get(Vein, vein_id)
        assert rock is not None
        assert rock.node_id == near_id, "жила должна была уйти в соседний узел"
        assert rock.roof == sag, "отказанный удар записал свой свод жиле, которая уехала"
        assert rock.remaining == rock_left, "закрытый забой всё-таки выбрал породу"
        mine = await db.get(Body, body_id)
        assert mine is not None
        assert float(mine.stamina) == stamina, "отказ списал выносливость"
        pocket = sum(
            amount_float(thing.amount)
            for thing in await world.contents(db, await world.body_container(db, mine))
            if thing.type_key == ORE
        )
        assert pocket == 9, f"вынесено не то, что было добыто: {pocket}"
        stuck = await world.contents(db, await mining.session_container(db, closed))
        assert not stuck, "руда легла в контейнер закрытой сессии — оттуда её не достать"
        assert refused, "удар по закрытому забою прошёл молча"


async def test_the_ground_waiting_at_the_vein_carries_out_the_swing_it_waited_for(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same pair the other way round, and nothing is refused.

    Here the swing takes the vein first and the eruption queues behind it. The
    swing is then working a face nobody has closed, so it must finish -- ore,
    roof and all -- and the job that follows must carry that ore out with the
    rest instead of leaving it in the face it is about to empty.

    This is the direction the reread must not touch: a check after the vein's
    lock that refused here would turn every eruption in a worked node into a
    lost swing, which is the opposite defect and a worse one -- it would fire
    on every pass of the tick rather than in the second of a collision.

    The handshake rides on `remember_roof`: the last thing a swing does with
    the vein in hand, and a call neither `leave` nor `abandon` ever makes, so
    the eruption arrives with the swing provably holding the lock.
    """
    from src.engine import mining, plates
    from src.engine.mining import face as mining_face
    from src.models.mining import MiningSession, Pace, SessionState
    from src.models.world import Layer, Planet

    holds_the_vein = asyncio.Event()
    stamping = mining_face.remember_roof

    async def held(*args, **kwargs):
        await stamping(*args, **kwargs)
        holds_the_vein.set()
        await asyncio.sleep(0.2)

    monkeypatch.setattr(mining_face, "remember_roof", held)
    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session, f"pyroxis.{stamp}", "Пироксис", planet=Planet.PYROXIS, area_m2=1, layer=Layer.SPACE
    )
    field = await world.create_node(
        session,
        f"pyroxis.{stamp}.field",
        "Чёрное поле",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
        parent=sphere,
    )
    vein = await world.create_vein(session, field, ORE, richness=70, remaining=100_000)
    who = await world.create_identity(session, f"Вахтовик-{stamp}")
    body = await world.print_body(session, who, field)
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=100)
    session.add(face)
    await session.flush()
    field_id, body_id, vein_id, face_id = field.id, body.id, vein.id, face.id
    await session.commit()

    async def swings() -> None:
        async with factory() as db, db.begin():
            own = await db.get(MiningSession, face_id)
            assert own is not None
            await mining.swing(db, current(), own)

    async def ground_moves() -> None:
        #: The swing is past the vein's lock and holding it -- by construction.
        await asyncio.wait_for(holds_the_vein.wait(), timeout=5)
        async with factory() as db, db.begin():
            #: The eruption's own order, mirrored from `plates.erupted`.
            await db.execute(
                select(Vein).where(Vein.node_id == field_id).order_by(Vein.id).with_for_update()
            )
            await db.execute(
                select(MiningSession)
                .join(Vein, Vein.id == MiningSession.vein_id)
                .where(Vein.node_id == field_id, MiningSession.state == SessionState.ACTIVE)
                .order_by(MiningSession.id)
                .with_for_update(of=MiningSession)
            )
            rock = await db.get(Vein, vein_id)
            assert rock is not None
            await plates._close_faces(db, current(), rock, now=datetime.now(UTC))

    outcome = await asyncio.gather(swings(), ground_moves(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        closed = await db.get(MiningSession, face_id)
        assert closed is not None and closed.state is SessionState.LEFT
        assert closed.swings == 1, "удар по открытому забою не засчитан"
        rock = await db.get(Vein, vein_id)
        assert rock is not None and rock.roof is not None
        assert float(rock.roof) < 100, "свод не просел от удара, который прошёл"
        mine = await db.get(Body, body_id)
        assert mine is not None
        pocket = sum(
            amount_float(thing.amount)
            for thing in await world.contents(db, await world.body_container(db, mine))
            if thing.type_key == ORE
        )
        assert pocket > 0, "добытое последним ударом осталось в закрытом забое"


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

    async def swings() -> None:
        async with factory() as db, db.begin():
            own = await db.get(MiningSession, face_id)
            assert own is not None
            await mining.swing(db, current(), own)

    async def leaves() -> None:
        #: The swing has laid its ore down and not yet written the session
        #: row -- by construction.
        await asyncio.wait_for(laid_the_ore.wait(), timeout=5)
        async with factory() as db, db.begin():
            own = await db.get(MiningSession, face_id)
            assert own is not None
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

    `timber` takes the vein first and the face's row second, the order the
    whole package keeps, and the timber in the pocket under its own lock --
    not because the vein's would not do here, but because a remainder guarded
    by somebody else's lock is guarded by a coincidence.

    The pause holds those locks: `body_container` is the first thing asked
    after them.
    """
    from src.engine import mining
    from src.engine.mining import face as mining_face
    from src.models.inventory import Item
    from src.models.mining import MiningSession, Pace

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
    #: Low enough that both supports raise the roof: two by
    #: `mine.roof_per_timber` from twenty stays under `mine.roof_timber_cap`.
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=20)
    session.add(face)
    await session.flush()
    pocket_id, face_id = pocket.id, face.id
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
        raised = 20 + 2 * constants[R.MINE_ROOF_PER_TIMBER]
        assert float(again.roof) == pytest.approx(raised), (
            f"свод поднят на одну стойку из двух: {float(again.roof)} вместо {raised}"
        )
        left = await db.scalar(
            select(Item).where(Item.container_id == pocket_id, Item.type_key == "shaft_support")
        )
        assert left is None, "две стойки поставлены из одного бревна"
