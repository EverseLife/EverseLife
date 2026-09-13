# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The automat family's tick against a hand over the canisters of its yard.

One of the race files (see `test_races.py` for the family's method). A reactor
distilling spirit takes its water out of the canisters standing in the yard,
and a field automaton waters its beds out of them. The tick lists the yard's
vessels before it locks anything, and the wait for its locks may carry one of
them off: whoever draws after the wait must ask again, under the vessel's own
lock, where the vessel is. And the yard may gain a vessel while the tick holds
it: whatever the tick pours into must be what it locked, or the new one is
taken after the stacks, against a pour taking it first. The handshake is
`automat_kit._until_blocked_by`: the side holding the contended rows lets go
only once the other side has provably walked into them.

A canister the fire takes is not raced here: the fire deletes what is inside
under the stacks' own lock (`world.destroy`), so the draw after the
wait finds nothing to take whether it asked or not.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agro_kit import field, growing, liquid_in, programmed, second_now
from automat_kit import LUBRICANT, _factory_floor, _learn, _lube_in, _until_blocked_by
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import agro, automat, liquid, stock, storage, world
from src.models.agro import FieldAutomat
from src.models.automat import Automat as AutomatRow
from src.models.farm import Plot
from src.models.identity import Body
from src.models.inventory import Container, Item
from src.units import amount_float

REACTOR = "auto_reactor"
SPIRIT = "alcohol"
CANISTER = "canister"
WATER = "water"

#: Units of water in each of the two canisters: light enough to be picked up,
#: enough for one unit of spirit. Sugar for more, so the water decides the output.
WATER_IN = 10.0
SUGAR = 4.0


async def test_the_tick_does_not_draw_out_of_a_canister_carried_off_during_the_wait(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The owner picks one water canister up while the tick waits for it.

    The tick listed the canister's inside while it stood in the yard and then
    waited on the canister, or on the water in it. The inside does not move
    with the canister, so a draw by that list took the water out of the
    owner's hands: the reactor made spirit out of a canister that was no
    longer standing at it.

    The second canister stays in the yard, so the machine has water for one
    unit whatever happens to the first: one unit is the right answer, two is
    the draw out of the hands, and none is a machine that did not work at all
    -- a failed advance is passed over (`automat.run._pass`), and a test
    asking only for "none" would pass on it.
    """
    _, yard, identity, body, reactor = await _factory_floor(
        session, constants, machine_kind=REACTOR
    )
    await world.grant_item(session, yard, "sugar", amount=SUGAR, quality=60, origin="test")
    lube, water, staying = [
        await world.grant_item(session, yard, CANISTER, quality=60, origin="test") for _ in range(3)
    ]
    #: An empty canister for the spirit: without room the reactor would stand
    #: whatever happened to the water.
    await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
    for can, name, units in (
        (lube, LUBRICANT, 10.0),
        (water, WATER, WATER_IN),
        (staying, WATER, WATER_IN),
    ):
        inside = await storage.inside(session, can)
        await world.grant_item(session, inside, name, amount=units, quality=55, origin="test")
    await _learn(session, identity, SPIRIT)
    row = await automat.program(session, constants, catalog, body, reactor, SPIRIT)
    (stack,) = await storage.content(session, water)
    body_id, water_id, stack_id, row_id = body.id, water.id, stack.id, row.id
    moment = row.counted_at + timedelta(hours=10)
    await session.commit()

    held = asyncio.Event()

    async def taker() -> None:
        async with factory() as db, db.begin():
            can = await db.get(Item, water_id)
            inner = await db.get(Item, stack_id)
            assert can is not None and inner is not None
            #: The canister and the water in it: whichever of the two the tick
            #: locks, it waits here until the canister is in the hands.
            await stock.lock_items(db, [can, inner])
            held.set()
            await _until_blocked_by(factory, db)
            me = await db.get(Body, body_id)
            assert me is not None
            await storage.pick(db, constants, catalog, me, can)

    async def tick() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            return await automat.tick_automats(db, constants, now=moment)

    _, made = await asyncio.gather(taker(), tick())

    assert made != pytest.approx(2), "the reactor made spirit out of water in the owner's hands"
    assert made == pytest.approx(1), "the machine did not work off the canister left standing"
    async with factory() as db:
        worked = await db.get(AutomatRow, row_id)
        assert worked is not None and worked.counted_at == moment
        can = await db.get(Item, water_id)
        me = await db.get(Body, body_id)
        assert can is not None and me is not None
        assert can.container_id == (await world.body_container(db, me)).id
        left = sum(amount_float(one.amount) for one in await storage.content(db, can))
        assert left == pytest.approx(WATER_IN)


#: Units of water in each of the field automaton's two canisters: one of them
#: waters the smallest bed it serves, and is still light enough to pick up.
BED_WATER = 40.0


async def test_the_field_automaton_does_not_water_out_of_a_canister_carried_off_during_the_wait(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The owner picks one water canister up while the field automaton waits for it.

    The machine drew the yard's lubricant and water in one lock over the yard
    and the insides of its vessels (`agro.run._advance`), listed before the
    wait. The inside does not move with the canister, so a draw by that list
    watered the bed out of the owner's hands. The second canister stays in the
    yard with water enough for the bed, and it is the one the watering must
    take: the bed watered out of the canister in the hands is the defect, the
    bed not watered at all a machine that did not work.
    """
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    moment = second_now()
    plot = await growing(session, constants, catalog, place.body, moment)
    target = constants[R.FARM_SOWN_MOISTURE] + constants[R.AGRO_MOISTURE_BAND] + 5
    #: The carried canister's water first in id order, so that a draw by the
    #: list reaches it before the one left standing.
    carried, staying = sorted(
        [await liquid_in(session, place.yard, WATER, BED_WATER, vessel=CANISTER) for _ in range(2)],
        key=lambda stack: stack.id,
    )
    can = await session.get(Container, carried.container_id)
    assert can is not None
    row = await programmed(
        session,
        constants,
        catalog,
        place,
        [{"do": "moisture", "target": target}, {"do": "harvest"}],
        [plot],
        moment,
    )
    body_id, can_id, carried_id, staying_id = place.body.id, can.owner_id, carried.id, staying.id
    row_id, plot_id = row.id, plot.id
    later = moment + timedelta(minutes=1)
    await session.commit()

    held = asyncio.Event()

    async def taker() -> None:
        async with factory() as db, db.begin():
            vessel = await db.get(Item, can_id)
            inner = await db.get(Item, carried_id)
            assert vessel is not None and inner is not None
            #: The canister and the water in it: whichever of the two the
            #: machine locks, it waits here until the canister is in the hands.
            await stock.lock_items(db, [vessel, inner])
            held.set()
            await _until_blocked_by(factory, db)
            me = await db.get(Body, body_id)
            assert me is not None
            await storage.pick(db, constants, catalog, me, vessel)

    async def tick() -> int:
        await held.wait()
        async with factory() as db, db.begin():
            return (await agro.tick_machines(db, constants, now=later)).actions

    _, actions = await asyncio.gather(taker(), tick())

    async with factory() as db:
        in_hands = await db.get(Item, carried_id)
        left = await db.get(Item, staying_id)
        vessel = await db.get(Item, can_id)
        me = await db.get(Body, body_id)
        assert in_hands is not None and vessel is not None and me is not None
        assert vessel.container_id == (await world.body_container(db, me)).id
        assert amount_float(in_hands.amount) == pytest.approx(BED_WATER), (
            "the bed was watered out of the canister in the owner's hands"
        )
        assert actions == 1 and left is not None and amount_float(left.amount) < BED_WATER, (
            "the machine did not water out of the canister left standing"
        )
        watered = await db.get(Plot, plot_id)
        worked = await db.get(FieldAutomat, row_id)
        assert watered is not None and float(watered.moisture) == pytest.approx(target)
        assert worked is not None and worked.counted_at == later


async def test_a_canister_put_down_during_the_tick_is_not_locked_at_its_payout(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The payout pours into the vessels the advance locked, and lists the yard no more.

    The tick locks the yard's vessels, then the lubricant and the inputs in
    them. The owner puts a fresh canister down meanwhile and starts filling it
    from the lubricant's canister: a pour takes both vessels in id order, the
    fresh one first, and waits on the other. The payout settled the spirit
    into the vessels "standing here" (`liquid.settle`), listed anew: it reached
    for the fresh canister the pour held while the pour waited on the canister
    the tick held, and the database killed one of the two -- the machine's
    hours put off, or the owner's pour failed outright.
    """
    _, yard, identity, body, reactor = await _factory_floor(
        session, constants, machine_kind=REACTOR
    )
    await world.grant_item(session, yard, "sugar", amount=SUGAR, quality=60, origin="test")
    water = await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
    inside = await storage.inside(session, water)
    await world.grant_item(session, inside, WATER, amount=WATER_IN, quality=55, origin="test")
    lube = await _lube_in(session, yard, 10)
    await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
    await _learn(session, identity, SPIRIT)
    row = await automat.program(session, constants, catalog, body, reactor, SPIRIT)
    lube_can = (await session.get(Container, lube.container_id)).owner_id
    body_id, row_id, yard_id = body.id, row.id, yard.id
    moment = row.counted_at + timedelta(hours=10)
    await session.commit()

    held = asyncio.Event()
    locked = liquid.lock_vessels

    async def holding(db: AsyncSession, vessels):
        rows = await locked(db, vessels)
        if not held.is_set():
            #: The tick holds the yard's vessels; the owner comes only now.
            held.set()
            await _until_blocked_by(factory, db)
        return rows

    monkeypatch.setattr(liquid, "lock_vessels", holding)

    async def put_down() -> uuid.UUID:
        """A canister put down in the yard, before the lubricant's in id order."""
        while True:
            async with factory() as db, db.begin():
                here = await db.get(Container, yard_id)
                assert here is not None
                fresh = await world.grant_item(db, here, CANISTER, quality=60, origin="test")
                if fresh.id < lube_can:
                    return fresh.id
                await db.delete(fresh)

    async def hand() -> float:
        await held.wait()
        fresh_id = await put_down()
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            assert me is not None
            _, poured = await liquid.pour(
                db,
                constants,
                catalog,
                me,
                await db.get(Item, lube_can),
                await db.get(Item, fresh_id),
                quantity=1,
            )
            return poured

    async def tick() -> float:
        async with factory() as db, db.begin():
            return await automat.tick_automats(db, constants, now=moment)

    poured, made = await asyncio.gather(hand(), tick())

    assert poured == pytest.approx(1)
    assert made == pytest.approx(1), "the machine's advance died waiting on the hand"
    async with factory() as db:
        worked = await db.get(AutomatRow, row_id)
        assert worked is not None and worked.counted_at == moment
        #: Into the yard's vessels, and none of it spilt.
        spirit = (await db.execute(select(Item).where(Item.type_key == SPIRIT))).scalars().all()
        assert sum(amount_float(one.amount) for one in spirit) == pytest.approx(1)
        assert all(one.container_id != yard_id for one in spirit)
