# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The automats' tick against a hand over a canister a liquid recipe draws from.

One of the race files (see `test_races.py` for the family's method). A reactor
distilling spirit takes its water out of the canisters standing in the yard. The
tick lists the yard's vessels before it locks anything, and the wait for its
locks may carry one of them off: whoever draws after the wait must ask again,
under the vessel's own lock, where the vessel is. The handshake is
`automat_kit._until_blocked_by`: the side holding the contended rows lets go
only once the other side has provably walked into them.

A canister the fire takes is not raced here: the fire deletes what is inside
under the stacks' own lock (`plates.fire._consume`), so the draw after the
wait finds nothing to take whether it asked or not.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import LUBRICANT, _factory_floor, _learn, _until_blocked_by
from src.constants import Catalog, Constants
from src.engine import automat, stock, storage, world
from src.models.automat import Automat as AutomatRow
from src.models.identity import Body
from src.models.inventory import Item
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
