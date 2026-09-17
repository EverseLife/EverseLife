# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A work, a hopper, a vent and the fire against a hand over the vessels they reach.

One of the race files (see `test_races.py` for the family's method). The rule
they all keep is `liquid.lock_vessels`: every vessel a transaction uses, in one
lock and id order, before any stack -- and after that lock only the vessels the
wait left where they were. A work draws its liquid inputs out of canisters and
may spend a vessel whole; a rig pours its hopper into them; a hand lets a vent
gas out of one; the fire burns a yard with vessels and sacks in it. Each of
them read or locked the vessels some other way and either met a pour head on,
or drew out of a canister already in somebody else's hands.

The handshake is `conftest._until_blocked_by`: the side holding the
contended rows lets go only once the other side has provably walked into them.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agro_kit import field, growing, liquid_in, programmed, second_now
from automat_kit import IRON, LUBRICANT, NAILS, _factory_floor, _learn
from conftest import _until_blocked_by
from lines_kit import CYLINDER, FLARE, HYDROGEN
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import agro, automat, craft, estate, liquid, rig, stock, storage, vent, wear, world
from src.engine.errors import Refusal
from src.engine.plates.fire import _burn
from src.models.agro import FieldAutomat
from src.models.automat import Automat as AutomatRow
from src.models.craft import CraftBatch
from src.models.estate import Building
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.inventory import Container, Item
from src.models.rig import Rig as RigRow
from src.models.world import Node
from src.units import amount_float

SPIRIT = "alcohol"
WATER = "water"
CANISTER = "canister"

#: Units of water in each of the two canisters a batch of spirit may draw on:
#: either of them is enough for the batch by hand, and light to carry.
WATER_IN = 50.0


async def test_a_batch_does_not_draw_out_of_a_canister_carried_off_during_the_wait(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A guest picks one water canister up while a master starting a batch waits for it.

    The work gathers its inputs off the yard and out of the vessels in it in
    one read, and locks what it read (`craft._internal._stock`). The water's
    inside does not move with its canister, so a draw by that read took the
    water out of the guest's hands. The second canister stays in the yard with
    water enough for the batch, and it is the one the batch must take: the
    worse water goes first, so a draw by the read reaches the carried canister.
    """
    node, yard, identity, master, _ = await _factory_floor(
        session, constants, machine_kind="fermentation_vat"
    )
    guest = await world.print_body(session, await world.create_identity(session, "Guest"), node)
    await _learn(session, identity, SPIRIT)
    pocket = await world.body_container(session, master)
    await world.grant_item(session, pocket, "sugar", amount=10, quality=60, origin="test")
    stacks = []
    for quality in (40, 60):
        can = await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
        inside = await storage.inside(session, can)
        stacks.append(
            await world.grant_item(
                session, inside, WATER, amount=WATER_IN, quality=quality, origin="test"
            )
        )
    carried, staying = stacks
    can = await session.get(Container, carried.container_id)
    assert can is not None
    master_id, guest_id, can_id = master.id, guest.id, can.owner_id
    carried_id, staying_id = carried.id, staying.id
    await session.commit()

    held = asyncio.Event()

    async def taker() -> None:
        async with factory() as db, db.begin():
            vessel = await db.get(Item, can_id)
            inner = await db.get(Item, carried_id)
            assert vessel is not None and inner is not None
            #: The canister and the water in it: whichever of the two the batch
            #: locks, it waits here until the canister is in the guest's hands.
            await stock.lock_items(db, [vessel, inner])
            held.set()
            await _until_blocked_by(factory, db)
            me = await db.get(Body, guest_id)
            assert me is not None
            await storage.pick(db, constants, catalog, me, vessel)

    async def work() -> CraftBatch:
        await held.wait()
        async with factory() as db, db.begin():
            me = await db.get(Body, master_id)
            assert me is not None
            return await craft.start(db, constants, catalog, me, SPIRIT, 1)

    _, batch = await asyncio.gather(taker(), work())

    assert batch.output == SPIRIT
    async with factory() as db:
        in_hands = await db.get(Item, carried_id)
        left = await db.get(Item, staying_id)
        vessel = await db.get(Item, can_id)
        me = await db.get(Body, guest_id)
        assert in_hands is not None and vessel is not None and me is not None
        assert vessel.container_id == (await world.body_container(db, me)).id
        assert amount_float(in_hands.amount) == pytest.approx(WATER_IN), (
            "the batch drew water out of the canister in the guest's hands"
        )
        assert left is None or amount_float(left.amount) < WATER_IN


async def _put_down_before(
    factory: async_sessionmaker[AsyncSession], yard_id: uuid.UUID, other: uuid.UUID
) -> uuid.UUID:
    """An empty canister put down in the yard and committed, before `other` in id order."""
    while True:
        async with factory() as db, db.begin():
            here = await db.get(Container, yard_id)
            assert here is not None
            fresh = await world.grant_item(db, here, CANISTER, quality=60, origin="test")
            if fresh.id < other:
                return fresh.id
            await db.delete(fresh)


async def test_a_hopper_is_not_poured_into_a_canister_put_down_after_its_vessels_lock(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emptying an oil hopper pours into the vessels it locked, and lists the node no more.

    The owner empties the hopper; its vessels are locked before the rig burns
    its coal (`rig._hold_vessels`). Meanwhile a canister is put down in the
    yard, and a second hand of the owner's starts filling it with water out of
    the canister beside it: a pour takes both in id order, the fresh one first,
    and waits on the other. The emptying poured "into the vessels in the hands,
    then those standing in the node" listed anew (`liquid.fill`): it reached
    for the fresh canister the pour held while the pour waited on the one it
    held, and the database killed one of the two.
    """
    node, yard, _, body, _ = await _factory_floor(session, constants)
    vein = await world.create_vein(session, node, "crude_oil", richness=55, remaining=100_000)
    await world.grant_item(session, yard, "coal", amount=100, quality=55, origin="test")
    source = await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
    inside = await storage.inside(session, source)
    await world.grant_item(session, inside, WATER, amount=10, quality=55, origin="test")
    await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
    pocket = await world.body_container(session, body)
    drill = await world.grant_item(session, pocket, "drilling_rig", quality=70, origin="test")
    well = await rig.place(session, body, drill, vein)
    moment = well.counted_at + timedelta(hours=8)
    body_id, well_id, source_id, yard_id = body.id, well.id, source.id, yard.id
    await session.commit()

    held = asyncio.Event()
    locked = liquid.lock_vessels

    async def holding(db: AsyncSession, vessels):
        rows = await locked(db, vessels)
        if not held.is_set():
            #: The emptying holds the node's vessels; the second hand comes only now.
            held.set()
            await _until_blocked_by(factory, db)
        return rows

    monkeypatch.setattr(liquid, "lock_vessels", holding)

    async def owner() -> float:
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            installation = await db.get(RigRow, well_id)
            assert me is not None and installation is not None
            return await rig.empty_hopper(db, constants, me, installation, now=moment)

    async def hand() -> float:
        await held.wait()
        fresh_id = await _put_down_before(factory, yard_id, source_id)
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            assert me is not None
            _, poured = await liquid.pour(
                db,
                constants,
                catalog,
                me,
                await db.get(Item, source_id),
                await db.get(Item, fresh_id),
                quantity=1,
            )
            return poured

    taken, poured = await asyncio.gather(owner(), hand())

    assert taken > 0, "the emptying died waiting on the hand"
    assert poured == pytest.approx(1), "the pour died waiting on the emptying"


async def test_a_hopper_is_not_poured_into_a_canister_carried_off_during_its_vessels_lock(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """Emptying an oil hopper pours only into the vessels its lock found in place.

    The vessels are listed and locked before the rig burns its coal, and the
    oil goes into them afterwards (`rig._hold_vessels`). A guest picks the first
    canister up while the emptying waits for it: poured by the list, the oil
    lands in the guest's hands past a carry limit nobody weighed it against.
    The second canister stays, and takes it.
    """
    node, yard, _, body, _ = await _factory_floor(session, constants)
    guest = await world.print_body(session, await world.create_identity(session, "Guest"), node)
    vein = await world.create_vein(session, node, "crude_oil", richness=55, remaining=100_000)
    await world.grant_item(session, yard, "coal", amount=100, quality=55, origin="test")
    cans = [
        await world.grant_item(session, yard, CANISTER, quality=60, origin="test") for _ in range(2)
    ]
    carried, staying = sorted(cans, key=lambda one: one.id)
    pocket = await world.body_container(session, body)
    drill = await world.grant_item(session, pocket, "drilling_rig", quality=70, origin="test")
    well = await rig.place(session, body, drill, vein)
    moment = well.counted_at + timedelta(hours=8)
    body_id, guest_id, well_id = body.id, guest.id, well.id
    carried_id, staying_id = carried.id, staying.id
    await session.commit()

    held = asyncio.Event()

    async def taker() -> None:
        async with factory() as db, db.begin():
            vessel = await db.get(Item, carried_id)
            assert vessel is not None
            await stock.lock_items(db, [vessel])
            held.set()
            await _until_blocked_by(factory, db)
            me = await db.get(Body, guest_id)
            assert me is not None
            await storage.pick(db, constants, catalog, me, vessel)

    async def owner() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            installation = await db.get(RigRow, well_id)
            assert me is not None and installation is not None
            return await rig.empty_hopper(db, constants, me, installation, now=moment)

    _, taken = await asyncio.gather(taker(), owner())

    assert taken > 0
    async with factory() as db:
        vessel = await db.get(Item, carried_id)
        me = await db.get(Body, guest_id)
        assert vessel is not None and me is not None
        assert vessel.container_id == (await world.body_container(db, me)).id
        assert await storage.content(db, vessel) == [], "the oil went into the guest's hands"
        oil = await storage.content(db, await db.get(Item, staying_id))
        assert sum(amount_float(one.amount) for one in oil) == pytest.approx(taken)


async def test_an_output_is_poured_into_a_canister_lifted_into_the_same_reach(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A liquid settling into the hands and the yard follows a canister between them.

    A batch's yield settles into the vessels in the master's hands, then those
    in the yard (`liquid.settle`). The one canister stands in the yard, and
    while the settling waits for it, it goes into those very hands. It is
    still within the reach the output was given -- passed over as "moved",
    the yield it had room for spilled.
    """
    _, yard, _, body, _ = await _factory_floor(session, constants)
    can = await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
    fresh = await world.grant_item(session, yard, SPIRIT, amount=2, quality=50, origin="test")
    pocket = await world.body_container(session, body)
    yard_id, pocket_id, can_id, fresh_id = yard.id, pocket.id, can.id, fresh.id
    await session.commit()

    held = asyncio.Event()

    async def lifter() -> None:
        async with factory() as db, db.begin():
            vessel = await db.get(Item, can_id)
            hands = await db.get(Container, pocket_id)
            assert vessel is not None and hands is not None
            await stock.lock_items(db, [vessel])
            held.set()
            await _until_blocked_by(factory, db)
            await world.move_stack(db, vessel, hands, 1)

    async def output() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            hands = await db.get(Container, pocket_id)
            here = await db.get(Container, yard_id)
            stack = await db.get(Item, fresh_id)
            assert hands is not None and here is not None and stack is not None
            return await liquid.settle(db, catalog, stack, [hands, here])

    _, spilled = await asyncio.gather(lifter(), output())

    assert spilled == 0, "the yield spilled beside a canister it had room in"
    async with factory() as db:
        vessel = await db.get(Item, can_id)
        assert vessel is not None and vessel.container_id == pocket_id
        inside = await storage.content(db, vessel)
        assert sum(amount_float(one.amount) for one in inside) == pytest.approx(2)


async def _paused_after_wear(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    held: asyncio.Event,
) -> None:
    """Stop the machine's advance right after its wear is written, until the fire walks in."""
    spent = wear.spend

    async def pausing(db: AsyncSession, *args, **kwargs) -> bool:
        gone = await spent(db, *args, **kwargs)
        if not held.is_set():
            #: The wear is written: the machine's row is the tick's now.
            await db.flush()
            held.set()
            await _until_blocked_by(factory, db)
        return gone

    monkeypatch.setattr(wear, "spend", pausing)


async def test_the_fire_does_not_meet_an_automat_holding_its_worn_machine(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The automat locks its yard's vessels before it writes its own wear.

    The eruption takes a yard's vessels first, then the rest of what lies there
    -- the machine among it. The tick wore its machine first and reached for the
    canister of lubricant only after: the fire held the canister and waited on
    the machine, the tick held the machine and waited on the canister.
    """
    node, yard, identity, body, machine = await _factory_floor(session, constants)
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    can = await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
    inside = await storage.inside(session, can)
    await world.grant_item(session, inside, LUBRICANT, amount=100, quality=55, origin="test")
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)
    node_id, row_id = node.id, row.id
    moment = row.counted_at + timedelta(hours=1)
    await session.commit()

    held = asyncio.Event()
    await _paused_after_wear(factory, monkeypatch, held)

    async def tick() -> float:
        async with factory() as db, db.begin():
            return await automat.tick_automats(db, constants, now=moment)

    async def fire() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            shaken = await db.get(Node, node_id)
            assert shaken is not None
            return await _burn(db, [shaken])

    made, burnt = await asyncio.gather(tick(), fire())

    assert made > 0, "the machine's advance died waiting on the fire"
    assert burnt > 0
    async with factory() as db:
        worked = await db.get(AutomatRow, row_id)
        assert worked is not None and worked.counted_at == moment


async def test_the_fire_does_not_meet_a_field_automaton_holding_its_worn_machine(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The field automaton locks its yard's vessels before it writes its own wear.

    The same crossing as the automat's: the fire held the canister of
    lubricant and waited on the machine, the machine's advance held its worn
    row and waited on the canister.
    """
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100, vessel=CANISTER)
    moment = second_now()
    plot = await growing(session, constants, catalog, place.body, moment)
    target = constants[R.FARM_SOWN_MOISTURE] + constants[R.AGRO_MOISTURE_BAND] + 5
    await liquid_in(session, place.yard, WATER, 40, vessel=CANISTER)
    row = await programmed(
        session,
        constants,
        catalog,
        place,
        [{"do": "moisture", "target": target}, {"do": "harvest"}],
        [plot],
        moment,
    )
    node_id, row_id = place.node.id, row.id
    later = moment + timedelta(minutes=1)
    await session.commit()

    held = asyncio.Event()
    await _paused_after_wear(factory, monkeypatch, held)

    async def tick() -> int:
        async with factory() as db, db.begin():
            return (await agro.tick_machines(db, constants, now=later)).actions

    async def fire() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            shaken = await db.get(Node, node_id)
            assert shaken is not None
            return await _burn(db, [shaken])

    actions, burnt = await asyncio.gather(tick(), fire())

    assert actions == 1, "the machine's advance died waiting on the fire"
    assert burnt > 0
    async with factory() as db:
        worked = await db.get(FieldAutomat, row_id)
        assert worked is not None and worked.counted_at == later


@pytest.mark.parametrize("kind", ["automat", "field_automaton"])
async def test_a_machine_taken_down_does_not_hold_its_yards_vessels(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    """A machine that will not draw this minute locks none of the yard's vessels.

    The tick's locks last until the whole world's pass commits, and every
    vessel of a yard it works in waits for that. A machine taken down works
    nothing and wears all the same: it holds its own row and no canister, so a
    hand pouring between two canisters beside it goes through while the pass
    is still under way.
    """
    if kind == "automat":
        _, yard, identity, body, machine = await _factory_floor(session, constants)
        await _learn(session, identity, NAILS)
        row = await automat.program(session, constants, catalog, body, machine, NAILS)
        moment = row.counted_at + timedelta(hours=1)
    else:
        place = await field(session, constants)
        yard, body, machine = place.yard, place.body, place.machine
        moment = second_now()
        plot = await growing(session, constants, catalog, place.body, moment)
        await programmed(session, constants, catalog, place, [{"do": "harvest"}], [plot], moment)
        moment += timedelta(minutes=1)
    source = await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
    inside = await storage.inside(session, source)
    await world.grant_item(session, inside, LUBRICANT, amount=10, quality=55, origin="test")
    target = await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
    machine.installed = False
    body_id, source_id, target_id = body.id, source.id, target.id
    await session.commit()

    started = asyncio.Event()
    blocked: list[bool] = []
    spent = wear.spend

    async def hand() -> float:
        await started.wait()
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            assert me is not None
            _, poured = await liquid.pour(
                db,
                constants,
                catalog,
                me,
                await db.get(Item, source_id),
                await db.get(Item, target_id),
                quantity=1,
            )
            return poured

    pouring = asyncio.ensure_future(hand())

    async def pausing(db: AsyncSession, *args, **kwargs) -> bool:
        gone = await spent(db, *args, **kwargs)
        if not started.is_set():
            #: The wear is written; the pass holds whatever it took so far.
            await db.flush()
            started.set()
            blocked.append(await _until_blocked_by(factory, db, unless=pouring))
        return gone

    monkeypatch.setattr(wear, "spend", pausing)

    async with factory() as db, db.begin():
        if kind == "automat":
            await automat.tick_automats(db, constants, now=moment)
        else:
            await agro.tick_machines(db, constants, now=moment)

    assert blocked == [False], "the pour waited for a machine that draws nothing"
    assert await pouring == pytest.approx(1)


async def _made_until(
    session: AsyncSession,
    make: Callable[[], Awaitable[Item]],
    fits: Callable[[Item], bool],
) -> Item:
    """A thing made again and again until its id fits: where a lock in id order meets it."""
    while True:
        made = await make()
        if fits(made):
            return made
        await session.delete(made)
        await session.flush()


async def test_the_fire_does_not_take_a_sack_before_a_canister_the_tick_holds(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fire locks a yard's vessels before the rest of what lies in it.

    The eruption burns a factory floor while the tick runs its nail machine.
    The tick takes the yard's vessels -- the lubricant is in one -- and then the
    iron on the floor. The fire took everything lying in the yard in one id
    order: the iron first, then the canister the tick held, while the tick
    waited on the iron. The database killed one of the two.
    """
    node, yard, identity, body, _ = await _factory_floor(session, constants)
    #: One id order over the yard reaches the iron before every row the tick
    #: holds by the time the fire comes: the canister, and the machine it wore.
    #: The floor's own machine stands idle, and the one programmed is made anew.
    iron = await _made_until(
        session,
        lambda: world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test"),
        lambda made: made.id.int < 1 << 127,
    )
    can = await _made_until(
        session,
        lambda: world.grant_item(session, yard, CANISTER, quality=60, origin="test"),
        lambda made: made.id > iron.id,
    )
    machine = await _made_until(
        session,
        lambda: world.grant_item(session, yard, "auto_station", quality=70, origin="test"),
        lambda made: made.id > iron.id,
    )
    inside = await storage.inside(session, can)
    await world.grant_item(session, inside, LUBRICANT, amount=100, quality=55, origin="test")
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)
    node_id, row_id = node.id, row.id
    moment = row.counted_at + timedelta(hours=1)
    await session.commit()

    held = asyncio.Event()
    locked = liquid.lock_vessels

    async def holding(db: AsyncSession, vessels):
        rows = await locked(db, vessels)
        if not held.is_set():
            #: The tick holds the yard's canister; the fire comes only now.
            held.set()
            await _until_blocked_by(factory, db)
        return rows

    monkeypatch.setattr(liquid, "lock_vessels", holding)

    async def tick() -> float:
        async with factory() as db, db.begin():
            return await automat.tick_automats(db, constants, now=moment)

    async def fire() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            shaken = await db.get(Node, node_id)
            assert shaken is not None
            return await _burn(db, [shaken])

    made, burnt = await asyncio.gather(tick(), fire())

    assert made > 0, "the machine's advance died waiting on the fire"
    assert burnt > 0
    async with factory() as db:
        worked = await db.get(AutomatRow, row_id)
        assert worked is not None and worked.counted_at == moment


async def test_a_falling_house_does_not_take_a_sack_before_a_canister_the_tick_holds(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A house falling takes what it buries the way the fire takes a field: the
    vessels first, then the rest (`estate.upkeep._bury`).

    The same factory floor under a roof one day from nothing. The fall took the
    iron first, in one id order over the floor, and then the canister the tick
    held, while the tick waited on the iron.
    """
    node, yard, identity, body, _ = await _factory_floor(session, constants)
    house = Building(node_id=node.id, area_m2=40)
    session.add(house)
    await session.flush()
    house.condition = Decimal(str(estate.decay_per_day(constants, house.kind)))
    iron = await _made_until(
        session,
        lambda: world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test"),
        lambda made: made.id.int < 1 << 127,
    )
    can = await _made_until(
        session,
        lambda: world.grant_item(session, yard, CANISTER, quality=60, origin="test"),
        lambda made: made.id > iron.id,
    )
    machine = await _made_until(
        session,
        lambda: world.grant_item(session, yard, "auto_station", quality=70, origin="test"),
        lambda made: made.id > iron.id,
    )
    inside = await storage.inside(session, can)
    await world.grant_item(session, inside, LUBRICANT, amount=100, quality=55, origin="test")
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)
    row_id = row.id
    moment = row.counted_at + timedelta(hours=1)
    await session.commit()

    held = asyncio.Event()
    locked = liquid.lock_vessels

    async def holding(db: AsyncSession, vessels):
        rows = await locked(db, vessels)
        if not held.is_set():
            #: The tick holds the floor's canister; the fall comes only now.
            held.set()
            await _until_blocked_by(factory, db)
        return rows

    monkeypatch.setattr(liquid, "lock_vessels", holding)

    async def tick() -> float:
        async with factory() as db, db.begin():
            return await automat.tick_automats(db, constants, now=moment)

    async def fall() -> int:
        await held.wait()
        async with factory() as db, db.begin():
            _, fallen = await estate.decay(db, constants)
            return fallen

    made, fallen = await asyncio.gather(tick(), fall())

    assert made > 0, "the machine's advance died waiting on the fall"
    assert fallen == 1
    async with factory() as db:
        worked = await db.get(AutomatRow, row_id)
        assert worked is not None and worked.counted_at == moment


@pytest.mark.parametrize(
    ("taken", "refused"), [("carried_off", "liquid-vessel-not-here"), ("burnt", "thing-gone")]
)
async def test_a_cylinder_taken_while_it_is_vented_is_not_emptied(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    taken: str,
    refused: str,
) -> None:
    """Emptying a vessel of its gas asks again after its lock where the vessel is.

    A chemist empties a cylinder of hydrogen standing on unowned ground into
    the flare while a passer-by picks the cylinder up, or the fire takes it.
    The reach was asked before the vessel's lock, and after the wait the
    hydrogen was let out of the passer-by's hands (`vent.empty`) -- or, the
    cylinder gone, its inside was said to be empty rather than the cylinder
    gone.
    """
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.flare.{stamp}", "Flare", area_m2=200)
    chemist = await world.print_body(
        session, await world.create_identity(session, f"Chemist-{stamp}"), node
    )
    lifter = await world.print_body(
        session, await world.create_identity(session, f"Lifter-{stamp}"), node
    )
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, FLARE, quality=60, origin="test")
    bottle = await world.grant_item(session, yard, CYLINDER, quality=60, origin="test")
    inside = await storage.inside(session, bottle)
    await world.grant_item(session, inside, HYDROGEN, amount=20, origin="test")
    chemist_id, lifter_id, bottle_id = chemist.id, lifter.id, bottle.id
    await session.commit()

    held = asyncio.Event()

    async def taker() -> None:
        async with factory() as db, db.begin():
            vessel = await db.get(Item, bottle_id)
            assert vessel is not None
            #: The row the pick and the fire lock, taken ahead to be held for the vent.
            await stock.lock_items(db, [vessel])
            held.set()
            await _until_blocked_by(factory, db)
            if taken == "burnt":
                await world.destroy(db, [vessel])
                return
            me = await db.get(Body, lifter_id)
            assert me is not None
            await storage.pick(db, constants, catalog, me, vessel)

    async def empty() -> tuple[str, float, str]:
        await held.wait()
        async with factory() as db, db.begin():
            me = await db.get(Body, chemist_id)
            vessel = await db.get(Item, bottle_id)
            assert me is not None and vessel is not None
            return await vent.empty(db, catalog, me, vessel)

    took, outcome = await asyncio.gather(taker(), empty(), return_exceptions=True)

    assert took is None, took
    assert isinstance(outcome, Refusal), outcome
    assert outcome.key == refused
    async with factory() as db:
        vented = await db.execute(select(Event.id).where(Event.kind == EventKind.STORAGE_VENTED))
        assert vented.first() is None, "the hydrogen was let out all the same"
        if taken == "burnt":
            return
        vessel = await db.get(Item, bottle_id)
        me = await db.get(Body, lifter_id)
        assert vessel is not None and me is not None
        assert vessel.container_id == (await world.body_container(db, me)).id
        gas = await storage.content(db, vessel)
        assert sum(amount_float(one.amount) for one in gas) == pytest.approx(20), (
            "the hydrogen was let out of the passer-by's hands"
        )
