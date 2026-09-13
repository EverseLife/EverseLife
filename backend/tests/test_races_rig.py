# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once on a drilling rig.

One of the race files (see `test_races.py` for the family's method): here the
contested things are the ones the rig tick holds for the whole world at once
(D-115) -- every rig row, the vein under each, the coal of its yard and the
machine it wears -- against the hands that reach for the same rows: a carrier
taking the coal, two carters emptying one hopper, an owner taking the machine
down, a hauler picking up the machine an owner is standing up, and a second
rig eating the same vein.

The lock order everyone here keeps is the `rig` module's: the rig row, then
its vein, then the machine and the coal of its yard in one statement by id. A
door that takes the machine before its row waits on the tick holding the row
while the tick waits on it for the wear, and the database kills one of the two
-- which is what the taking-down door did until the two races on it below.
What else takes those rows -- a falling house, the fire, the station doors --
is `test_races_rig_order.py`'s.

Cut out of `test_races_mining.py`, whose subject is the ore the hands dig,
when the second of those races took it past the length the quality bar allows
one file.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import _until_blocked_by
from conftest import _slow
from src.constants import current, current_catalog
from src.engine import stock, world
from src.models.identity import Body
from src.models.inventory import Item

ORE = "iron_ore"
#: Seconds before an event-ordered race is called hung rather than slow: a
#: later change that makes one side wait on a row the other holds would
#: otherwise leave `gather` waiting forever, where `_until_blocked_by` fails.
_HUNG = 30


async def _rig_before_its_pass(
    session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, datetime]:
    """A rig standing on its vein with coal in the yard, four hours unsettled.

    Nothing mined yet: the committed hopper is nought, and the pass that fills
    it is the one the taking-down races. Ids and the tick's moment, not rows:
    each side of a race reads its own.
    """
    from src.engine import rig

    stamp = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.pit.{stamp}", "Забой", area_m2=200)
    vein = await world.create_vein(session, node, ORE, richness=60, remaining=100_000)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "coal", amount=1000, quality=55, origin="тест")
    identity = await world.create_identity(session, f"Промышленник-{stamp}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    machine = await world.grant_item(session, pocket, "drilling_rig", quality=70, origin="тест")
    installation = await rig.place(session, body, machine, vein)
    assert float(installation.hopper) == 0
    ids = (body.id, machine.id, installation.id, installation.counted_at + timedelta(hours=4))
    await session.commit()
    return ids


async def _take_down(factory: async_sessionmaker[AsyncSession], body_id, machine_id) -> str:
    """The taking-down door in a transaction of its own, and what it answered."""
    from src.engine import station

    async with factory() as db, db.begin():
        own_body = await db.get(Body, body_id)
        own_machine = await db.get(Item, machine_id)
        try:
            await station.take(db, current_catalog(), own_body, own_machine)
        except station.NotEmpty:
            return "refused"
        return "taken"


async def test_burning_coal_and_carrying_it_away_at_once_keep_the_count(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared yard: the tick burns coal while a player picks it up. Both
    read the stack, both write it -- without the lock one write is lost and
    coal is either doubled or vanishes (wave 2, item 4a)."""
    from src.engine import rig
    from src.units import amount as to_units

    stamp = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.yard.{stamp}", "Двор", area_m2=100)
    yard = await world.node_container(session, node)
    coal = await world.grant_item(session, yard, "coal", amount=10, origin="тест")
    machine = await world.grant_item(
        session, yard, "drilling_rig", quality=70, origin="тест", installed=True
    )
    identity = await world.create_identity(session, f"Носильщик-{stamp}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    await session.commit()
    #: The tick counts the coal, and the carry commits before the tick locks:
    #: the stack the tick already holds in memory is stale by then.
    _slow(monkeypatch, rig, "_coal_available")

    async def burn() -> None:
        async with factory() as db, db.begin():
            #: As the tick does: count the coal first, then take the machine and
            #: the coal and burn it. The count loads the stack into the session
            #: before the lock; the lock must reread it, or the burn writes from
            #: the value before the carry.
            assert await rig._coal_available(db, yard.id) >= 4
            _, stacks = await rig._held(db, machine.id, yard.id)
            await stock.consume(db, stacks, to_units(4))

    async def carry() -> None:
        async with factory() as db, db.begin():
            own = await db.get(Item, coal.id)
            target = await db.get(type(pocket), pocket.id)
            await world.move_stack(db, own, target, 3)

    await asyncio.gather(burn(), carry())
    rows = (
        await session.execute(select(Item.container_id, Item.amount).where(Item.type_key == "coal"))
    ).all()
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


async def test_two_empties_of_one_ore_hopper_hand_over_each_unit_once(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ore hopper became money-shaped when it started giving out **part** of
    itself (D-314): all or nothing could only ever leave it at nought, and now
    two hands reaching in at once must not each be handed the same units. The
    rig row is taken `with_for_update`, so the second empties what the first left.
    """
    from src.engine import gear, rig

    stamp = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.pit.{stamp}", "Забой", area_m2=200)
    vein = await world.create_vein(session, node, "iron_ore", richness=60, remaining=100_000)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "coal", amount=10_000, quality=55, origin="тест")
    identity = await world.create_identity(session, f"Промышленник-{stamp}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    machine = await world.grant_item(session, pocket, "drilling_rig", quality=70, origin="тест")
    installation = await rig.place(session, body, machine, vein)
    #: A hopper heavier than the hands, pinned to one moment: both empties
    #: advance to the same "now", so time adds nothing between them, and
    #: neither call can take the lot.
    moment = installation.counted_at + timedelta(hours=100)
    mined = await rig.advance(session, current(), installation, now=moment)
    room = await gear.room_for(session, current(), current_catalog(), body, "iron_ore")
    assert room < mined, "тест требует бункер тяжелее рук"
    await session.commit()
    _slow(monkeypatch, rig, "advance")

    async def take() -> float:
        async with factory() as db, db.begin():
            own_body = await db.get(Body, body.id)
            own_rig = await db.get(type(installation), installation.id)
            with contextlib.suppress(gear.Overloaded):
                return await rig.empty_hopper(db, current(), own_body, own_rig, now=moment)
            return 0.0

    taken = await asyncio.gather(take(), take())

    from src.units import amount as to_units

    carried = sum(
        int(row.amount)
        for row in (
            await session.execute(
                select(Item).where(Item.container_id == pocket.id, Item.type_key == "iron_ore")
            )
        )
        .scalars()
        .all()
    )
    left = await session.scalar(
        select(type(installation).hopper).where(type(installation).id == installation.id)
    )
    assert carried == to_units(sum(taken)), "в руках ровно столько, сколько отдано вызовами"
    assert carried + to_units(float(left)) == to_units(mined), (
        "каждая единица руды выдана ровно один раз: бункер плюс руки сходятся с добытым"
    )
    assert 0 < carried < to_units(mined), "руки взяли часть, остальное ждёт в бункере"


async def test_a_rig_is_not_taken_down_out_from_under_the_tick(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The taking-down door reads the hopper to refuse a loaded machine (D-181,
    D-314), and the tick holds every rig row of the world in one uncommitted
    transaction. Read without the lock, the door would see the last committed
    nought while a whole pass already stands in the row -- and hand the loaded
    machine over through the very rule it was asked for.

    The tick lets go only once the door provably waits on it, holding every
    rig row and what their passes write (`rig._hold_the_world`); the door takes
    the node and then the rig row, so what it waits on is the row itself. A
    fixed pause here let a loaded run reach the machine before the tick did --
    the other interleaving, which is the next test's.
    """
    from src.engine import rig

    body_id, machine_id, _, moment = await _rig_before_its_pass(session)
    held = asyncio.Event()
    advance = rig.advance

    async def holding(db: AsyncSession, *args, **kwargs) -> float:
        if not held.is_set():
            held.set()
            await _until_blocked_by(factory, db)
        return await advance(db, *args, **kwargs)

    monkeypatch.setattr(rig, "advance", holding)

    async def tick() -> float:
        async with factory() as db, db.begin():
            return await rig.tick_rigs(db, current(), now=moment)

    async def take() -> str:
        await held.wait()
        return await _take_down(factory, body_id, machine_id)

    mined, verdict = await asyncio.gather(tick(), take())

    assert mined > 0, "тик намыл руду в том же окне"
    assert verdict == "refused", "снятие увидело намытое, а не последний закоммиченный ноль"
    installed = await session.scalar(select(Item.installed).where(Item.id == machine_id))
    assert installed is True, "машина осталась стоять"


async def test_a_rig_taken_down_ahead_of_the_tick_is_not_drilled(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The door reaches the machine before the tick does -- the interleaving
    that deadlocked. The tick takes every rig row and last the machine's own,
    which `wear.spend` writes; the door took the machine first and the rig row
    after it, for the hopper. Each then held what the other reached for, and
    the database killed one of the two: a player taking a rig down during the
    tick lost the command, or the tick's pass for every rig in the world rolled
    back and waited for its retry.

    The door holds what it has taken until the tick provably waits on it. The
    right to the place is asked with the machine already in hand in either
    order, so the handshake sits there. Taken in the tick's order, the door
    finishes first, and the tick finds the machine lying: it drills nothing
    and settles only its stamp.
    """
    from src.engine import rig, station
    from src.models.rig import Rig

    body_id, machine_id, rig_id, moment = await _rig_before_its_pass(session)
    held = asyncio.Event()
    asked = station.may_build

    async def holding(db: AsyncSession, *args, **kwargs) -> bool:
        if not held.is_set():
            held.set()
            await _until_blocked_by(factory, db)
        return await asked(db, *args, **kwargs)

    monkeypatch.setattr(station, "may_build", holding)

    async def tick() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            return await rig.tick_rigs(db, current(), now=moment)

    verdict, mined = await asyncio.gather(_take_down(factory, body_id, machine_id), tick())

    assert verdict == "taken", "в закоммиченном бункере ноль: машину сняли"
    assert mined == 0, "снятая машина не бурит"
    installed = await session.scalar(select(Item.installed).where(Item.id == machine_id))
    assert installed is False, "машина лежит там, где стояла"
    hopper, counted = (
        await session.execute(select(Rig.hopper, Rig.counted_at).where(Rig.id == rig_id))
    ).one()
    assert float(hopper) == 0, "бункер пуст"
    assert counted == moment, "тик сдвинул только метку"


async def _rig_lying_by_the_vein(
    session: AsyncSession, *, row: bool
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """A rig lying on the floor of nobody's pit, and two bodies at it.

    With `row` it stood once and was taken down, so its row waits for it and
    the door puts it back up; without, it never stood -- the first placement,
    where no row is locked before the machine's. The second body wears a
    charged frame: 44 kg does not come off the floor without one (D-268).
    Ids, not rows: each side of a race reads its own.
    """
    from overload_kit import EXO, _charged
    from src.engine import gear, rig, station

    stamp = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.pit.{stamp}", "Забой", area_m2=200)
    vein = await world.create_vein(session, node, ORE, richness=60, remaining=100_000)
    owner = await world.create_identity(session, f"Промышленник-{stamp}")
    body = await world.print_body(session, owner, node)
    if row:
        pocket = await world.body_container(session, body)
        machine = await world.grant_item(session, pocket, "drilling_rig", quality=70, origin="тест")
        await rig.place(session, body, machine, vein)
        await station.take(session, current_catalog(), body, machine)
    else:
        yard = await world.node_container(session, node)
        machine = await world.grant_item(
            session, yard, "drilling_rig", quality=70, origin="тест", installed=False
        )
    hauler = await world.print_body(
        session, await world.create_identity(session, f"Грузчик-{stamp}"), node
    )
    frame = await world.grant_item(
        session, await world.body_container(session, hauler), EXO, quality=60, origin="тест"
    )
    await _charged(session, hauler)
    await gear.equip(session, current(), current_catalog(), hauler, frame)
    ids = (owner.id, hauler.id, machine.id, vein.id)
    await session.commit()
    return ids


async def _stand_by_command(
    factory: async_sessionmaker[AsyncSession], identity_id, machine_id, vein_id
) -> str | None:
    """The `rig.place` command in a transaction of its own: its refusal key, or None."""
    import src.api.session  # noqa: F401 -- registers the commands
    from src.api.registry import COMMANDS
    from src.engine.errors import Refusal

    try:
        async with factory() as db, db.begin():
            await COMMANDS["rig.place"].run(
                {"identity_id": identity_id},
                db,
                {"cmd": "rig.place", "item": str(machine_id), "vein": str(vein_id)},
            )
    except Refusal as refusal:
        return refusal.key
    return None


@pytest.mark.parametrize("row", [True, False], ids=["put_back_up", "first_placement"])
async def test_a_rig_picked_up_under_the_place_door_is_not_stood_up(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    row: bool,
) -> None:
    """The command reads free whether the machine is in the hands or lying here,
    and the door writes it standing at the flush. A hauler picking the lying
    machine up in between committed a move the door then wrote over: the rig
    stood on the vein, out of the pocket it had just gone into -- the pick
    lost, and with it the machine.

    The door holds no row the pick needs while it waits here (the placer's body
    is not the hauler's), so the pick runs to its commit and an event is an
    exact order, not a pause. What must see that commit is the door's own lock
    on the machine, taken after the rig row -- and with no row at all, first.
    """
    from src.engine import rig, storage
    from src.models.rig import Rig

    owner_id, hauler_id, machine_id, vein_id = await _rig_lying_by_the_vein(session, row=row)
    looked = asyncio.Event()
    picked = asyncio.Event()
    place = rig.place

    async def after_the_pick(db: AsyncSession, *args, **kwargs):
        looked.set()
        await picked.wait()
        return await place(db, *args, **kwargs)

    monkeypatch.setattr(rig, "place", after_the_pick)

    async def pick() -> None:
        await looked.wait()
        try:
            async with factory() as db, db.begin():
                hauler = await db.get(Body, hauler_id)
                machine = await db.get(Item, machine_id)
                await storage.pick(db, current(), current_catalog(), hauler, machine)
        finally:
            picked.set()

    verdict, _ = await asyncio.wait_for(
        asyncio.gather(_stand_by_command(factory, owner_id, machine_id, vein_id), pick()),
        _HUNG,
    )

    hands = await world.body_container(session, await session.get(Body, hauler_id))
    where, installed = (
        await session.execute(
            select(Item.container_id, Item.installed).where(Item.id == machine_id)
        )
    ).one()
    assert verdict == "station-not-in-hands", "дверь увидела подъём, а не свой старый взгляд"
    assert where == hands.id, "машина в руках у грузчика"
    assert installed is False, "и не стоит"
    rows = await session.scalar(
        select(func.count()).select_from(Rig).where(Rig.item_id == machine_id)
    )
    assert rows == (1 if row else 0), "строка буровой не заведена и не потеряна"


async def test_a_rig_burnt_under_the_place_door_is_refused_in_words(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same gap from the world's side: the machine is gone between the
    command's look and the door's write -- a burnt yard, a fallen house. The
    door used to write on a row that was no longer there, and the player read a
    server failure where the truth is that the rig is gone.
    """
    from src.engine import rig

    owner_id, _, machine_id, vein_id = await _rig_lying_by_the_vein(session, row=True)
    looked = asyncio.Event()
    burnt = asyncio.Event()
    place = rig.place

    async def after_the_fire(db: AsyncSession, *args, **kwargs):
        looked.set()
        await burnt.wait()
        return await place(db, *args, **kwargs)

    monkeypatch.setattr(rig, "place", after_the_fire)

    async def burn() -> None:
        await looked.wait()
        try:
            async with factory() as db, db.begin():
                await db.delete(await db.get(Item, machine_id))
        finally:
            burnt.set()

    verdict, _ = await asyncio.wait_for(
        asyncio.gather(_stand_by_command(factory, owner_id, machine_id, vein_id), burn()),
        _HUNG,
    )

    assert verdict == "rig-machine-gone", "сказано словами: машины больше нет"
    assert await session.scalar(select(Item.id).where(Item.id == machine_id)) is None


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
