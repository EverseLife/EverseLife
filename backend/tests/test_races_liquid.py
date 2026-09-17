# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over the vessels a liquid is poured between.

One of the race files (see `test_races.py` for the family's method): here the
contended thing is a vessel (D-230) -- its room, the order it is locked in, and
the place it lies in.

**The room** is measured off what lies **inside**, while the lock the pours
queue on is the vessel's own row -- so whoever fills a vessel after waiting for
it must measure it after the wait (`liquid.lock_vessels`), whatever it read of
the vessel before. The first two tests stand for no path a caller is known to
take today: every caller weighs a vessel off what its own lock reread. They pin
the door, so that measuring after the wait does not rest on who happens to keep
a row or an answer across it. The third pins the same rule one lock further
on: the counter splits the stacks of a canister it has just locked, and a stack
held from before that wait must be reread there too.

**The order**: every vessel a transaction will use, in one lock and id order,
and before any stack. A pour takes its vessels and then the stacks in them; the
automats' tick, a batch's end and a rig's hopper each took theirs some other
way round and met a pour, or each other, head on.

**The place**: the vessels are listed before their lock, and the wait may
carry one off or burn it. Whoever pours after the wait asks again.

The handshake is `automat_kit._until_blocked_by`: the side holding the
contended rows lets go only once the other side has provably walked into them.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import LUBRICANT, _factory_floor, _learn, _lube_in, _until_blocked_by
from market_kit import _city, _trader
from src.constants import Catalog, Constants
from src.engine import automat, craft, jobs, liquid, market, rig, stock, storage, world
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.inventory import Container, Item
from src.models.job import Job, JobState
from src.models.rig import Rig as RigRow
from src.models.world import Node
from src.units import amount, amount_float

REACTOR = "auto_reactor"
SPIRIT = "alcohol"
CANISTER = "canister"

#: Units of spirit: already in the target, in the canister the hand empties
#: into it, and made by the reactor -- sugar for exactly that much, so the
#: inputs rather than the clock decide the output.
INSIDE = 4.0
POURED = 2.0
MADE = 4.0


async def _distillery(session: AsyncSession, constants: Constants, catalog: Catalog):
    """A reactor distilling spirit on the automat tests' floor, two canisters of it by.

    Water and lubricant stand in canisters of their own, and the two spirit
    canisters hold stacks nothing tells apart, so a pour from one into the
    other folds them (D-214). Returns the body, the automat's row, the target
    and the source -- the target first in id order, the order the tick pours
    into the vessels.
    """
    _, yard, identity, body, reactor = await _factory_floor(
        session, constants, machine_kind=REACTOR
    )
    await world.grant_item(session, yard, "sugar", amount=MADE, quality=60, origin="test")
    water = await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
    inside = await storage.inside(session, water)
    await world.grant_item(session, inside, "water", amount=50, quality=60, origin="test")
    await _lube_in(session, yard, 10)
    await _learn(session, identity, SPIRIT)
    row = await automat.program(session, constants, catalog, body, reactor, SPIRIT)

    cans = [
        await world.grant_item(session, yard, CANISTER, quality=60, origin="test") for _ in range(2)
    ]
    target, source = sorted(cans, key=lambda can: can.id)
    for can, units in ((target, INSIDE), (source, POURED)):
        inside = await storage.inside(session, can)
        await world.grant_item(session, inside, SPIRIT, amount=units, quality=55, origin="test")
    return body, row, target, source


async def _spirit_in(session: AsyncSession, vessel: Item) -> float:
    return sum(
        amount_float(thing.amount)
        for thing in await storage.content(session, vessel)
        if thing.type_key == SPIRIT
    )


async def test_a_row_held_from_before_the_wait_does_not_set_a_vessels_room(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A liquid output measures the vessel after its lock, not off a row read before.

    The owner empties one spirit canister into the other while the reactor's
    output settles into the same one. The tick's transaction saw the source
    before the pour committed and still holds that row -- standing for any
    frame of a caller that keeps one. The pour folded the target's stack into
    the arriving one (D-214), so the row held is the one now inside the
    target, with the source's amount. Measured off that copy, the target
    seemed to take the whole output and ended over the brim by what it already
    held. Measured after the wait, it takes what fits, and the rest goes into
    the canister the pour emptied -- nothing spills either.
    """
    unit = catalog.recipes.mass_of(SPIRIT)
    limit = storage.capacity(catalog, CANISTER)
    assert limit is not None
    #: What the race needs of the vault, asserted rather than assumed: after
    #: the pour the target cannot take the whole output, and by the stack the
    #: tick read before the pour it could.
    assert (INSIDE + POURED + MADE) * unit > limit >= (POURED + MADE) * unit

    body, row, target, source = await _distillery(session, constants, catalog)
    body_id, target_id, source_id = body.id, target.id, source.id
    moment = row.counted_at + timedelta(hours=10)
    await session.commit()

    held = asyncio.Event()
    locked = liquid.lock_vessels

    async def holding(db: AsyncSession, vessels):
        rows = await locked(db, vessels)
        if not held.is_set():
            #: The hand holds both canisters now; the tick starts only after.
            held.set()
            await _until_blocked_by(factory, db)
        return rows

    monkeypatch.setattr(liquid, "lock_vessels", holding)

    async def hand() -> float:
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
            )
            return poured

    async def tick() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            #: The source canister as this transaction saw it before the pour,
            #: and kept: a clean row stays in the session only while something
            #: refers to it, and the forecast's own copies go with the
            #: command's memory at the payout's flush. Held here, the test does
            #: not rest on the garbage collector.
            seen = await storage.content(db, await db.get(Item, source_id))
            assert [amount_float(thing.amount) for thing in seen] == [POURED]
            return await automat.tick_automats(db, constants, now=moment)

    poured, made = await asyncio.gather(hand(), tick())

    assert poured == pytest.approx(POURED)
    assert made == pytest.approx(MADE)
    async with factory() as db:
        target = await db.get(Item, target_id)
        source = await db.get(Item, source_id)
        assert target is not None and source is not None
        assert await storage.stored_mass(db, catalog, target) <= limit + 1e-6
        #: Nothing spilled and nothing doubled: all of it lies in the two canisters.
        held_now = await _spirit_in(db, target) + await _spirit_in(db, source)
        assert held_now == pytest.approx(INSIDE + POURED + MADE, abs=0.01)
        spills = (
            await db.execute(select(Event).where(Event.kind == EventKind.STORAGE_SPILLED))
        ).scalars()
        assert not list(spills)


async def test_a_vessel_remembered_before_a_pour_is_measured_after_it(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The room under the lock is read anew, not answered from the command's memory.

    One transaction weighs the target and goes on without writing; another
    empties the source into it and commits. The first then asks how much its
    vessels take: the lock does not wait -- the pour is over -- but the answer
    remembered before it still names the stack the pour swallowed, with the
    target's old amount, and whatever acts on that room pours past the brim.
    No caller asks in this order today; the test keeps the door from relying
    on that.
    """
    unit = catalog.recipes.mass_of(SPIRIT)
    limit = storage.capacity(catalog, CANISTER)
    assert limit is not None
    #: The pour itself must fit, or the room below is not the sum it reads as.
    assert (INSIDE + POURED) * unit <= limit
    body, _, target, source = await _distillery(session, constants, catalog)
    body_id, target_id, source_id = body.id, target.id, source.id
    await session.commit()

    async with factory() as early, early.begin():
        weighed = await liquid.free_in(early, catalog, await early.get(Item, target_id))
        assert weighed == pytest.approx(limit - INSIDE * unit)
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            assert me is not None
            await liquid.pour(
                db,
                constants,
                catalog,
                me,
                await db.get(Item, source_id),
                await db.get(Item, target_id),
            )
        me = await early.get(Body, body_id)
        assert me is not None
        yard = await world.node_container(early, await early.get(Node, me.node_id))
        room = await liquid.room_for(early, catalog, yard, SPIRIT)

    #: What the pour left in the target, and the whole of the source it emptied.
    assert room * unit == pytest.approx(limit - (INSIDE + POURED) * unit + limit)


async def test_a_load_at_the_counter_does_not_write_over_a_draw_from_the_same_canister(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The counter splits a canister's stack off the amount under its lock.

    A seller pours lubricant from the canister in the hands into the terminal's
    tank (D-255) while a draw -- a batch taking its input out of that canister
    (`liquid.locked_stacks`) -- holds the same stack. The seller's transaction
    saw the canister before the draw committed and still holds the stack; the
    load waits for it at the counter's lock, the draw commits. Split off the
    amount read before the wait, the stack went back to what it held before
    the draw, and the lubricant drawn was in the canister again as well as in
    the batch.
    """
    fill, drawn, loaded = 20.0, 5.0, 4.0
    node = await _city(session)
    _, body = await _trader(session, node, "Seller")
    pocket = await world.body_container(session, body)
    canister = await world.grant_item(session, pocket, CANISTER, quality=60, origin="test")
    inside = await storage.inside(session, canister)
    await world.grant_item(session, inside, LUBRICANT, amount=fill, quality=55, origin="test")
    body_id, canister_id, inside_id = body.id, canister.id, inside.id
    await session.commit()

    held = asyncio.Event()

    async def draw() -> None:
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            assert me is not None
            hands = await world.body_container(db, me)
            stacks = await liquid.locked_stacks(db, catalog, hands, (LUBRICANT,))
            held.set()
            await _until_blocked_by(factory, db)
            assert await stock.consume(db, stacks, amount(drawn)) == amount(drawn)

    async def load() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            assert me is not None
            #: The canister as this transaction saw it before the draw, and
            #: kept -- as in the first test, so the race does not rest on the
            #: garbage collector.
            seen = await storage.content(db, await db.get(Item, canister_id))
            assert [amount_float(thing.amount) for thing in seen] == [fill]
            return await market.load(db, constants, me, LUBRICANT, loaded)

    _, poured = await asyncio.gather(draw(), load())

    assert poured == pytest.approx(loaded)
    async with factory() as db:
        left = (
            (await db.execute(select(Item).where(Item.container_id == inside_id))).scalars().all()
        )
    assert sum(amount_float(stack.amount) for stack in left) == pytest.approx(fill - drawn - loaded)


#: Units of lubricant a hand draws off a canister in the tests of the lock order.
DRAWN = 2.0


async def _reactor_yard(
    session: AsyncSession, constants: Constants, catalog: Catalog, *, spare: int
):
    """A reactor distilling spirit, its lubricant and water in canisters, `spare` empty ones by.

    The canisters are made first and given their part by id afterwards, so
    the order the tick walks them in is the test's and not the dice's: the
    lubricant's lowest, the water's next, the empty ones after. Returns the
    body, the automat's row and the canisters in that order.
    """
    _, yard, identity, body, reactor = await _factory_floor(
        session, constants, machine_kind=REACTOR
    )
    await world.grant_item(session, yard, "sugar", amount=MADE, quality=60, origin="test")
    cans = [
        await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
        for _ in range(2 + spare)
    ]
    lube, water, *spares = sorted(cans, key=lambda can: can.id)
    for can, name, units in ((lube, LUBRICANT, 10.0), (water, "water", 50.0)):
        inside = await storage.inside(session, can)
        await world.grant_item(session, inside, name, amount=units, quality=55, origin="test")
    await _learn(session, identity, SPIRIT)
    row = await automat.program(session, constants, catalog, body, reactor, SPIRIT)
    return body, row, lube, water, spares


async def _no_spills(db: AsyncSession) -> bool:
    found = await db.execute(select(Event.id).where(Event.kind == EventKind.STORAGE_SPILLED))
    return found.first() is None


async def test_a_pour_out_of_the_lubricant_canister_does_not_deadlock_the_tick(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tick locks the yard's vessels before the lubricant it draws out of them.

    The owner fills a canister in the hands from the machine's lubricant while
    the reactor's spirit settles into the canister beside it. A pour takes the
    vessels, then the stacks in them (`liquid.pour`); the tick took the
    lubricant stack first and reached for its canister only at the payout,
    walking the yard's vessels for room. Each then waited on the other, and
    the database killed one of the two: the machine's hours put off to the
    next tick, or the owner's pour failed outright.
    """
    body, row, lube, _, spares = await _reactor_yard(session, constants, catalog, spare=1)
    pocket = await world.body_container(session, body)
    hands = await world.grant_item(session, pocket, CANISTER, quality=60, origin="test")
    body_id, lube_id, hands_id, target_id = body.id, lube.id, hands.id, spares[0].id
    moment = row.counted_at + timedelta(hours=10)
    await session.commit()

    held = asyncio.Event()
    locked = liquid.lock_vessels

    async def holding(db: AsyncSession, vessels):
        rows = await locked(db, vessels)
        if not held.is_set():
            #: The hand holds the lubricant's canister; the tick starts only now.
            held.set()
            await _until_blocked_by(factory, db)
        return rows

    monkeypatch.setattr(liquid, "lock_vessels", holding)

    async def hand() -> float:
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            assert me is not None
            _, poured = await liquid.pour(
                db,
                constants,
                catalog,
                me,
                await db.get(Item, lube_id),
                await db.get(Item, hands_id),
                quantity=DRAWN,
            )
            return poured

    async def tick() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            return await automat.tick_automats(db, constants, now=moment)

    poured, made = await asyncio.gather(hand(), tick())

    assert poured == pytest.approx(DRAWN)
    assert made == pytest.approx(MADE), "the machine's advance died waiting on the hand"
    async with factory() as db:
        assert await _spirit_in(db, await db.get(Item, target_id)) == pytest.approx(MADE)
        drawn = await storage.content(db, await db.get(Item, hands_id))
        assert sum(amount_float(thing.amount) for thing in drawn) == pytest.approx(DRAWN)
        assert await _no_spills(db)


async def test_a_batch_pouring_into_the_hands_and_the_yard_does_not_deadlock_a_pour_between_them(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A liquid output locks every vessel within reach at once, in id order.

    A batch of spirit ends while its master pours from the canister in the
    hands into one standing in the yard -- the yard's is the lower id. The
    output fills the hands first and the yard after (`finish`), and the vessels
    were locked in that order too, one at a time: the batch held the canister
    in the hands and reached for the yard's, the pour held the yard's and
    reached for the one in the hands.

    No caller meets this today: the command takes the body's row for the pour
    (`_alive`) and the batch's end takes it first, so the two queue on the
    body before either touches a vessel. The test pins the door's own order,
    so that the vessels do not rest on the body for it.
    """
    #: Spirit in the hands, in the yard and made: the room left in the hands is
    #: less than the batch makes, so the output must go on to the yard.
    batch_units, in_hands, in_yard = 2.0, 7.0, 1.0
    unit = catalog.recipes.mass_of(SPIRIT)
    limit = storage.capacity(catalog, CANISTER)
    assert limit is not None
    assert 0 < limit - in_hands * unit < batch_units * unit

    _, yard, identity, body, _ = await _factory_floor(
        session, constants, machine_kind="fermentation_vat"
    )
    await _learn(session, identity, SPIRIT)
    pocket = await world.body_container(session, body)
    #: By hand a recipe takes more than its norm, so the sugar is not counted out.
    await world.grant_item(session, pocket, "sugar", amount=10, quality=60, origin="test")
    #: The water stands in the yard: the hands carry the spirit, and the carry
    #: limit would not take both.
    water = await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
    inside = await storage.inside(session, water)
    await world.grant_item(session, inside, "water", amount=50, quality=60, origin="test")
    cans = [
        await world.grant_item(session, yard, CANISTER, quality=60, origin="test") for _ in range(2)
    ]
    standing, carried = sorted(cans, key=lambda can: can.id)
    await world.move_stack(session, carried, pocket, 1)
    for can, units in ((carried, in_hands), (standing, in_yard)):
        inside = await storage.inside(session, can)
        await world.grant_item(session, inside, SPIRIT, amount=units, quality=55, origin="test")
    batch = await craft.start(session, constants, catalog, body, SPIRIT, batch_units)
    ready, body_id, carried_id, standing_id = batch.ready_at, body.id, carried.id, standing.id
    await session.commit()

    held = asyncio.Event()
    locked = liquid.lock_vessels

    async def holding(db: AsyncSession, vessels):
        rows = await locked(db, vessels)
        if not held.is_set() and any(vessel.id == carried_id for vessel in vessels):
            #: The batch holds the canister in the hands; the pour starts only now.
            held.set()
            await _until_blocked_by(factory, db)
        return rows

    monkeypatch.setattr(liquid, "lock_vessels", holding)

    async def worker() -> Job | None:
        return await jobs.run_one(factory, now=ready)

    async def hand() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            assert me is not None
            _, poured = await liquid.pour(
                db,
                constants,
                catalog,
                me,
                await db.get(Item, carried_id),
                await db.get(Item, standing_id),
                quantity=1,
            )
            return poured

    job, poured = await asyncio.gather(worker(), hand())

    assert job is not None and job.state is JobState.DONE, job and job.last_error
    assert poured == pytest.approx(1)
    async with factory() as db:
        carried = await db.get(Item, carried_id)
        standing = await db.get(Item, standing_id)
        assert carried is not None and standing is not None
        together = await _spirit_in(db, carried) + await _spirit_in(db, standing)
        assert together == pytest.approx(in_hands + in_yard + batch_units, abs=0.01)
        for can in (carried, standing):
            assert await storage.stored_mass(db, catalog, can) <= limit + 1e-6
        assert await _no_spills(db)


async def test_an_oil_hopper_emptied_beside_a_furnace_does_not_deadlock_the_tick(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emptying a liquid hopper locks the vessels before the coal the rig burns.

    An oil rig and a glass furnace automat stand on one floor and burn the
    same coal. The owner empties the hopper into the canister in the yard
    while the tick runs the furnace. The tick takes the yard's vessels before
    any stack -- its lubricant is in one of them; emptying settled the rig
    first, burning its coal, and reached for the canister only to pour.
    """
    node, yard, identity, body, furnace = await _factory_floor(
        session, constants, machine_kind="auto_furnace"
    )
    vein = await world.create_vein(session, node, "crude_oil", richness=55, remaining=100_000)
    await world.grant_item(session, yard, "coal", amount=100, quality=55, origin="test")
    await world.grant_item(session, yard, "quartz_sand", amount=40, quality=55, origin="test")
    await _lube_in(session, yard, 10)
    canister = await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
    pocket = await world.body_container(session, body)
    drill = await world.grant_item(session, pocket, "drilling_rig", quality=70, origin="test")
    well = await rig.place(session, body, drill, vein)
    await _learn(session, identity, "glass")
    row = await automat.program(session, constants, catalog, body, furnace, "glass")
    moment = max(row.counted_at, well.counted_at) + timedelta(hours=8)
    body_id, well_id, canister_id = body.id, well.id, canister.id
    await session.commit()

    held = asyncio.Event()
    #: The rig burns out of the stacks its pass locked (`rig._held`), through
    #: the one write-off every consumer shares; the owner goes first, so the
    #: first burn is the rig's.
    burned = stock.consume

    async def holding(db: AsyncSession, *args) -> int:
        taken = await burned(db, *args)
        if not held.is_set():
            #: The rig's coal is burnt and held; the tick starts only now.
            held.set()
            await _until_blocked_by(factory, db)
        return taken

    monkeypatch.setattr(stock, "consume", holding)

    async def owner() -> float:
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            installation = await db.get(RigRow, well_id)
            assert me is not None and installation is not None
            return await rig.empty_hopper(db, constants, me, installation, now=moment)

    async def tick() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            return await automat.tick_automats(db, constants, now=moment)

    taken, made = await asyncio.gather(owner(), tick())

    assert taken > 0
    #: Sand for twenty panes, and coal for them and the rig both.
    assert made == pytest.approx(20), "the furnace's advance died waiting on the rig"
    async with factory() as db:
        oil = await storage.content(db, await db.get(Item, canister_id))
        assert sum(amount_float(thing.amount) for thing in oil) == pytest.approx(taken)


#: What takes a canister off the yard while a pour waits for it: a hand picking
#: it up (the floor is open, D-204), or the fire (`world.destroy`), which takes
#: it out of the world with what is in it.
TAKEN = ("carried_off", "burnt")


async def _take_away(
    db: AsyncSession, constants: Constants, catalog: Catalog, taken: str, can: Item, taker_id
) -> None:
    if taken == "burnt":
        await world.destroy(db, [can])
    else:
        taker = await db.get(Body, taker_id)
        assert taker is not None
        await storage.pick(db, constants, catalog, taker, can)


async def _gone_as_it_should_be(db: AsyncSession, taken: str, can_id, taker_id) -> None:
    """The canister taken away holds what it held, and nobody made it a new inside."""
    if taken == "burnt":
        orphan = await db.execute(select(Container.id).where(Container.owner_id == can_id))
        assert orphan.first() is None, "an inside was made for a canister that is gone"
        return
    taker = await db.get(Body, taker_id)
    can = await db.get(Item, can_id)
    assert taker is not None and can is not None
    assert can.container_id == (await world.body_container(db, taker)).id
    assert await _spirit_in(db, can) == pytest.approx(1), "poured into the hands that took it"


@pytest.mark.parametrize("taken", TAKEN)
async def test_the_tick_neither_counts_nor_fills_a_canister_taken_during_the_wait(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    taken: str,
) -> None:
    """The tick weighs and fills only the vessels still standing once it has them.

    The owner picks a canister up -- or the fire takes it -- while the tick
    waits for it. The vessels were listed before the wait, and the tick
    poured into the one it had listed: into the owner's hands, past the carry
    limit the pick had weighed without it, or into an inside made anew for a
    canister that no longer existed -- nothing ties an inside to a living
    owner -- and out of the world with no spill to say so. After the wait the
    canister is out of the forecast too: the other one has room for less than
    the inputs would make, and counted in, the room it no longer offered made
    spirit that spilled.
    """
    in_first, in_second = 1.0, 6.0
    unit = catalog.recipes.mass_of(SPIRIT)
    limit = storage.capacity(catalog, CANISTER)
    assert limit is not None
    room = (limit - in_second * unit) / unit
    assert 0 < room < MADE

    body, row, _, _, spares = await _reactor_yard(session, constants, catalog, spare=2)
    first, second = spares
    for can, units in ((first, in_first), (second, in_second)):
        inside = await storage.inside(session, can)
        await world.grant_item(session, inside, SPIRIT, amount=units, quality=55, origin="test")
    body_id, first_id, second_id = body.id, first.id, second.id
    moment = row.counted_at + timedelta(hours=10)
    await session.commit()

    held = asyncio.Event()

    async def taker() -> None:
        async with factory() as db, db.begin():
            can = await db.get(Item, first_id)
            assert can is not None
            #: The row the pick and the fire lock, taken ahead to be held for the tick.
            await stock.lock_items(db, [can])
            held.set()
            await _until_blocked_by(factory, db)
            await _take_away(db, constants, catalog, taken, can, body_id)

    async def tick() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            return await automat.tick_automats(db, constants, now=moment)

    _, made = await asyncio.gather(taker(), tick())

    assert made == pytest.approx(room, abs=1e-3)
    async with factory() as db:
        await _gone_as_it_should_be(db, taken, first_id, body_id)
        standing = await _spirit_in(db, await db.get(Item, second_id))
        assert standing == pytest.approx(in_second + room, abs=1e-3)
        assert await _no_spills(db)


@pytest.mark.parametrize("taken", TAKEN)
async def test_a_liquid_output_is_not_poured_into_a_canister_taken_during_the_wait(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    taken: str,
) -> None:
    """The door every liquid output settles through asks where its vessels are after the wait.

    The tick locks the yard's vessels long before it pours, so its own pour
    never waits; a batch's end, a rig's hopper and a print do (`liquid.fill`).
    Here a stack settles into the yard (`liquid.settle`) while a guest picks
    the first canister up, or the fire takes it: the stack must go into the
    second one and nowhere else.
    """
    node, yard, _, _, _ = await _factory_floor(session, constants)
    guest = await world.print_body(session, await world.create_identity(session, "Guest"), node)
    cans = [
        await world.grant_item(session, yard, CANISTER, quality=60, origin="test") for _ in range(2)
    ]
    first, second = sorted(cans, key=lambda can: can.id)
    inside = await storage.inside(session, first)
    await world.grant_item(session, inside, SPIRIT, amount=1, quality=55, origin="test")
    fresh = await world.grant_item(session, yard, SPIRIT, amount=MADE, quality=50, origin="test")
    yard_id, guest_id, first_id, second_id, fresh_id = (
        yard.id,
        guest.id,
        first.id,
        second.id,
        fresh.id,
    )
    await session.commit()

    held = asyncio.Event()

    async def taker() -> None:
        async with factory() as db, db.begin():
            can = await db.get(Item, first_id)
            assert can is not None
            await stock.lock_items(db, [can])
            held.set()
            await _until_blocked_by(factory, db)
            await _take_away(db, constants, catalog, taken, can, guest_id)

    async def output() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            here = await db.get(Container, yard_id)
            stack = await db.get(Item, fresh_id)
            assert here is not None and stack is not None
            return await liquid.settle(db, catalog, stack, [here])

    _, spilled = await asyncio.gather(taker(), output())

    assert spilled == 0
    async with factory() as db:
        await _gone_as_it_should_be(db, taken, first_id, guest_id)
        assert await _spirit_in(db, await db.get(Item, second_id)) == pytest.approx(MADE)


@pytest.mark.parametrize(
    ("taken", "refused"), [("carried_off", "liquid-vessel-not-here"), ("burnt", "thing-gone")]
)
async def test_a_canister_taken_during_a_pour_is_not_poured_from(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    taken: str,
    refused: str,
) -> None:
    """A pour asks again after the wait where its vessels are.

    The owner pours from a canister standing in the yard while a guest picks
    that canister up, or the fire takes it. The reach was asked before the
    vessels' lock, and after the wait the pour drew the lubricant out of the
    guest's hands -- or, the canister gone, said its inside was empty.
    """
    node, yard, _, owner, _ = await _factory_floor(session, constants)
    guest = await world.print_body(session, await world.create_identity(session, "Guest"), node)
    standing = await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
    inside = await storage.inside(session, standing)
    await world.grant_item(session, inside, LUBRICANT, amount=DRAWN, quality=55, origin="test")
    pocket = await world.body_container(session, owner)
    hands = await world.grant_item(session, pocket, CANISTER, quality=60, origin="test")
    owner_id, guest_id, standing_id, hands_id = owner.id, guest.id, standing.id, hands.id
    await session.commit()

    held = asyncio.Event()

    async def taker() -> None:
        async with factory() as db, db.begin():
            can = await db.get(Item, standing_id)
            assert can is not None
            await stock.lock_items(db, [can])
            held.set()
            await _until_blocked_by(factory, db)
            await _take_away(db, constants, catalog, taken, can, guest_id)

    async def pourer() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            me = await db.get(Body, owner_id)
            assert me is not None
            _, poured = await liquid.pour(
                db,
                constants,
                catalog,
                me,
                await db.get(Item, standing_id),
                await db.get(Item, hands_id),
            )
            return poured

    took, outcome = await asyncio.gather(taker(), pourer(), return_exceptions=True)

    assert took is None, took
    assert isinstance(outcome, liquid.LiquidError), outcome
    assert outcome.key == refused
    async with factory() as db:
        drawn = await storage.content(db, await db.get(Item, hands_id))
        assert drawn == [], "nothing reached the owner's canister"
