# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over a vessel a work is about to spend.

One of the race files (see `test_races.py` for the family's method). The
contended thing is a pot: a work spends it only empty (D-344), and a pour can
land in it on either side of the write-off -- before it, and the work must see
the water; after it, and the pour must find no pot. The handshake is
`_until_blocked_by`: whichever side holds the pot lets go only once the other
has provably walked into it.

Every door that pours into a vessel is raced against the batch that spends
it: a hand pouring (`liquid.pour`), a batch's output settling
(`liquid.settle`, the path the automat and the rig take too) and a liquid
taken off the terminal (`market.take`).
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _until_blocked_by
from market_kit import TERMINAL, _trader
from src.constants import Catalog, Constants
from src.engine import craft, liquid, market, storage, world
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node
from src.units import amount_float, money
from storage_kit import (
    ACID,
    BATTERY,
    CANISTER,
    LEAD,
    POT,
    WATER,
    _bench,
    _orphaned,
    _there,
    _vessel,
    _water_in,
)


async def test_water_poured_into_the_pot_under_a_battery_keeps_the_pot(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The batch reads the pot empty, the pour fills it and commits, and only
    then does the batch get the pot's row.

    Asked before the lock, "is anything inside" was answered off the empty pot
    the batch had read, and the pot went into the battery with the water just
    poured into it. Asked under the lock, it is answered after the pour: every
    door into a vessel takes the vessel's row first (`liquid._lock`), so the
    batch waits for the pour and then sees the water. The only pot is full,
    and the battery is refused.
    """
    _, _, body, yard = await _bench(session)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, LEAD, amount=5, quality=60, origin="test")
    await world.grant_item(session, pocket, ACID, amount=5, quality=60, origin="test")
    pot = await _vessel(session, yard, POT, water=0)
    canister = await _vessel(session, yard, CANISTER, water=3)
    body_id, pot_id, canister_id = body.id, pot.id, canister.id
    await session.commit()

    poured = asyncio.Event()

    async def batch() -> None:
        await poured.wait()
        async with factory() as db, db.begin():
            #: A lock the pour still held would hang here: fail fast instead.
            await db.execute(text("SET LOCAL lock_timeout = '10s'"))
            me = await db.get(Body, body_id)
            assert me is not None
            await craft.start(db, constants, catalog, me, BATTERY, 1)

    started = asyncio.create_task(batch())

    async def pour() -> bool:
        try:
            async with factory() as db, db.begin():
                me = await db.get(Body, body_id)
                source = await db.get(Item, canister_id)
                target = await db.get(Item, pot_id)
                assert me is not None and source is not None and target is not None
                await liquid.pour(db, constants, catalog, me, source, target, WATER, 2)
                poured.set()
                return await _until_blocked_by(factory, db, unless=started)
        finally:
            #: A pour that failed must not leave the batch waiting for ever: the
            #: test fails on what happened instead of hanging the run.
            poured.set()

    waited, outcome = await asyncio.gather(pour(), started, return_exceptions=True)

    assert waited is True, "the batch never walked into the pot the pour holds"
    assert isinstance(outcome, craft.NotEnough), outcome
    assert outcome.key == "craft-not-enough-empty"
    async with factory() as db:
        assert await _there(db, pot_id), "the pot poured full did not go into the battery"
        assert await _water_in(db, pot_id) == pytest.approx(2)
        assert await _water_in(db, canister_id) == pytest.approx(1)
        assert await _orphaned(db) == 0


async def test_a_pour_waiting_on_a_pot_the_battery_spent_pours_nowhere(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The other order: the batch takes the empty pot and writes it off, the
    pour waits on its row, and the batch commits.

    The lock skips a deleted row, and the pot's inside outlives it -- tied by
    id, not by a foreign key. A pour that went on after the wait measured the
    room of a pot that was gone, found its old inside and poured the water into
    it, out of the world. It is told the pot is gone instead, and the water
    stays in the canister.
    """
    _, _, body, yard = await _bench(session)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, LEAD, amount=5, quality=60, origin="test")
    await world.grant_item(session, pocket, ACID, amount=5, quality=60, origin="test")
    pot = await _vessel(session, yard, POT, water=0)
    canister = await _vessel(session, yard, CANISTER, water=3)
    body_id, pot_id, canister_id = body.id, pot.id, canister.id
    await session.commit()

    spent = asyncio.Event()

    async def pour() -> None:
        await spent.wait()
        async with factory() as db, db.begin():
            await db.execute(text("SET LOCAL lock_timeout = '10s'"))
            me = await db.get(Body, body_id)
            source = await db.get(Item, canister_id)
            target = await db.get(Item, pot_id)
            assert me is not None and source is not None and target is not None
            await liquid.pour(db, constants, catalog, me, source, target, WATER, 2)

    pouring = asyncio.create_task(pour())

    async def batch() -> bool:
        try:
            async with factory() as db, db.begin():
                me = await db.get(Body, body_id)
                assert me is not None
                await craft.start(db, constants, catalog, me, BATTERY, 1)
                spent.set()
                return await _until_blocked_by(factory, db, unless=pouring)
        finally:
            #: A batch that failed must not leave the pour waiting for ever.
            spent.set()

    waited, outcome = await asyncio.gather(batch(), pouring, return_exceptions=True)

    assert waited is True, "the pour never walked into the pot the batch holds"
    assert isinstance(outcome, liquid.LiquidError), outcome
    assert outcome.key == "thing-gone"
    async with factory() as db:
        assert not await _there(db, pot_id), "the pot went into the battery"
        assert await _water_in(db, canister_id) == pytest.approx(3)
        assert await _orphaned(db) == 0


async def _spending(factory, constants, catalog, body_id, spent: asyncio.Event, other) -> bool:
    """The battery that takes the empty pot, held open until `other` waits on it."""
    try:
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            assert me is not None
            await craft.start(db, constants, catalog, me, BATTERY, 1)
            spent.set()
            return await _until_blocked_by(factory, db, unless=other)
    finally:
        #: A batch that failed must not leave the other side waiting for ever.
        spent.set()


async def test_output_settling_into_a_pot_the_battery_spent_spills(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A batch's liquid output pours into the vessels at hand (`liquid.settle`),
    and the pot it reaches for is being spent: it waits on the pot's row, and
    the battery commits.

    The vessel is skipped, not poured into: its inside outlives it, and the
    water would have gone into a place that no longer exists. With no other
    vessel here the output spills -- the end of a term cannot refuse (D-230).
    """
    _, _, body, yard = await _bench(session)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, LEAD, amount=5, quality=60, origin="test")
    await world.grant_item(session, pocket, ACID, amount=5, quality=60, origin="test")
    pot = await _vessel(session, yard, POT, water=0)
    body_id, pot_id, yard_id = body.id, pot.id, yard.id
    await session.commit()

    spent = asyncio.Event()

    async def output() -> float:
        await spent.wait()
        async with factory() as db, db.begin():
            await db.execute(text("SET LOCAL lock_timeout = '10s'"))
            place = await db.get(type(yard), yard_id)
            assert place is not None
            made = await world.grant_item(db, place, WATER, amount=2, origin="test")
            return await liquid.settle(db, catalog, made, (place,))

    settling = asyncio.create_task(output())
    waited, spilled = await asyncio.gather(
        _spending(factory, constants, catalog, body_id, spent, settling),
        settling,
        return_exceptions=True,
    )

    assert waited is True, "the output never walked into the pot the batch holds"
    assert spilled == pytest.approx(2), spilled
    async with factory() as db:
        assert not await _there(db, pot_id)
        assert await _orphaned(db) == 0


async def test_a_liquid_taken_off_the_terminal_into_a_pot_being_spent_stays_in_the_tank(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The master takes the water they bought off the terminal into the pot in
    their hands (`market.take`), and their own battery is spending that pot:
    the take waits on the pot's row, and the battery commits.

    Poured by the list read before the lock, the water went into the spent
    pot's inside and out of the world, bought and paid for. Poured by what the
    lock hands back, there is no vessel with room, and the water waits in the
    tank (D-255).
    """
    node, _, _, yard = await _bench(session)
    await world.grant_item(session, yard, TERMINAL, quality=70, origin="test")
    seller_identity, seller = await _trader(session, node, "Seller")
    can = await _vessel(session, await world.body_container(session, seller), CANISTER, water=0)
    await world.grant_item(
        session, await storage.inside(session, can), WATER, amount=10, quality=55, origin="test"
    )
    await market.load(session, constants, seller, WATER, 3)
    tier = market.tier_of(constants, 55)
    await market.sell(
        session,
        constants,
        catalog,
        seller_identity,
        node,
        type_key=WATER,
        tier=tier,
        price=money(1),
        quantity=3,
    )

    master_identity, master = await _trader(session, node, "Master", funds=100)
    await world.learn(session, master_identity, BATTERY)
    pocket = await world.body_container(session, master)
    await world.grant_item(session, pocket, LEAD, amount=5, quality=60, origin="test")
    await world.grant_item(session, pocket, ACID, amount=5, quality=60, origin="test")
    pot = await _vessel(session, pocket, POT, water=0)
    fill = await market.buy(
        session, constants, catalog, master, type_key=WATER, tier=tier, price=money(1), quantity=3
    )
    assert fill.traded == pytest.approx(3)
    body_id, pot_id, node_id = master.id, pot.id, node.id
    await session.commit()

    spent = asyncio.Event()

    async def taking() -> float:
        await spent.wait()
        async with factory() as db, db.begin():
            await db.execute(text("SET LOCAL lock_timeout = '10s'"))
            me = await db.get(Body, body_id)
            assert me is not None
            return await market.take(db, constants, me, WATER, 3)

    take = asyncio.create_task(taking())
    waited, outcome = await asyncio.gather(
        _spending(factory, constants, catalog, body_id, spent, take),
        take,
        return_exceptions=True,
    )

    assert waited is True, "the take never walked into the pot the batch holds"
    assert isinstance(outcome, market.NoRoom), outcome
    async with factory() as db:
        assert not await _there(db, pot_id)
        assert await _orphaned(db) == 0
        me = await db.get(Body, body_id)
        place = await db.get(Node, node_id)
        assert me is not None and place is not None
        cell = await market.stall(db, place, me.identity_id, create=False)
        assert cell is not None
        kept = await db.execute(select(Item.amount).where(Item.container_id == cell.id))
        assert sum(amount_float(value) for value in kept.scalars().all()) == pytest.approx(3)
