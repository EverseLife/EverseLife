# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once on the same diggings.

One of the race files (see `test_races.py` for the family's method): here the
contested thing is what the ground gives up and what the hands do with it --
a vein two picks swing at, a body two sockets spend, a face one socket works
while another walks out of it, a coal heap the tick burns while a hand
carries it, an oil hopper two carters empty. Remainders of matter, raced the
same way money is.

A face against what closes it from outside -- a death, the moving ground -- is
a race about the place and lives in `test_races_face.py`.
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
from src.constants import registry as R
from src.engine import stock, world
from src.models.identity import Body
from src.models.inventory import Item
from src.units import amount_float

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
    sag, stamina = float(late.roof), float((await session.get(Body, late_body_id)).stamina)
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
        assert float(late_again.roof) == sag, "свод просел от удара, который ничего не добыл"
        body = await db.get(Body, late_body_id)
        assert body is not None
        assert float(body.stamina) == stamina, "выносливость списана за пустой удар"
        assert not await world.contents(db, await mining.session_container(db, late_again))
        assert refused, "удар по выработанной жиле прошёл молча"


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
    #: One swing from nought: both arrive at the collapse or neither does.
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=1)
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


async def test_two_rigs_on_one_vein_bank_only_what_the_ground_gave(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rig plans against a free read of the vein; it may bank only what is there.

    Each rig caps its hours by what the vein holds, and the ore that cap
    allows but the hopper cannot show waits in `hopper_remainder` -- a column
    that cannot hold a whole unit. The plan is made before the vein is locked,
    so the second rig here plans against a vein the first has since emptied:
    its hours promise ten, the clamp under the lock gives it nothing to bank,
    and the difference is the sliver's to keep. It does not fit. The throw
    comes out of `tick_rigs`, where every rig in the world shares one
    transaction, so a single exhausted vein would stop the tick for everybody
    -- which is why the sliver is bounded by what the ground can still give
    and not merely by what was asked for.
    """
    from src.constants import registry as R
    from src.engine import rig
    from src.models.rig import Rig
    from src.models.world import Vein
    from src.units import amount as to_units

    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.pit.{stamp}", "Забой", area_m2=200)
    #: Less in the ground than the two machines together would raise in their
    #: hour: whoever locks the vein second finds it empty, or nearly.
    vein = await world.create_vein(session, node, ORE, richness=60, remaining=20)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "coal", amount=100, quality=55, origin="тест")
    identity = await world.create_identity(session, f"Промышленник-{stamp}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    rigs = []
    for _ in range(2):
        machine = await world.grant_item(session, pocket, "drilling_rig", quality=70, origin="тест")
        installation = await rig.place(session, body, machine, vein)
        installation.counted_at = installation.counted_at - timedelta(hours=1)
        rigs.append(installation.id)
    await session.commit()
    start = await session.scalar(select(Vein.remaining).where(Vein.id == vein.id))

    #: Between the vein's free read and its lock, in the order the defect had.
    _slow(monkeypatch, rig, "_coal_available")

    async def settle(rig_id: uuid.UUID) -> None:
        async with factory() as db, db.begin():
            #: The prologue of `rig.empty`: the rig's own row, then the vein.
            own = await db.get(Rig, rig_id)
            await db.refresh(own, with_for_update=True)
            await rig.advance(db, current(), own, now=datetime.now(UTC))

    await asyncio.gather(*(settle(r) for r in rigs))

    left = await session.scalar(select(Vein.remaining).where(Vein.id == vein.id))
    #: Columns, not entities: the session's own copies of these rows predate
    #: the two commits above and would answer with what it remembers.
    held = (
        await session.execute(
            select(Rig.hopper, Rig.hopper_remainder, Rig.fuel_remainder).where(Rig.id.in_(rigs))
        )
    ).all()
    banked = sum(float(hopper) for hopper, _, _ in held)
    assert start - left == to_units(banked * current()[R.RIG_DEPLETION_MULTIPLIER]), (
        "жила отдала ровно столько, сколько лежит в бункерах"
    )
    assert banked > 0, "машины действительно работали"
    for _, ore, coal in held:
        assert 0 <= float(ore) < 0.001, "осколок руды меньше тысячной"
        assert 0 <= float(coal) < 0.001, "осколок угля меньше тысячной"


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
    face = MiningSession(
        body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=Decimal(str(start))
    )
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
        raised = min(constants[R.MINE_ROOF_TIMBER_CAP], start + 2 * per_timber)
        assert float(again.roof) == pytest.approx(raised), (
            f"свод поднят на одну стойку из двух: {float(again.roof)} вместо {raised}"
        )
        left = await db.scalar(
            select(Item).where(Item.container_id == pocket_id, Item.type_key == "shaft_support")
        )
        assert left is None, "две стойки поставлены из одного бревна"
