# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over the automats' tick, where a node's own stop
stands the machine: cut off for non-payment (D-149) or frozen (D-231).

Cut out of `test_races_automat.py`, which keeps the races of machines at work.
The tick reads both stops without a lock and before it takes anything of the
yard, and keeps its transaction open for the rest of the world's factories:
what these pin is that a player acting on the stopped floor meanwhile -- paying
the debt, taking the lubricant, drawing the pool -- never waits for the step.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import IRON, NAILS, _factory_floor, _learn, _lube_in, _on_aurora
from src.constants import Catalog, Constants
from src.engine import automat, utility, world
from src.models.automat import Automat as AutomatRow
from src.models.city import UtilityMeter
from src.models.energy import EnergyPool
from src.models.identity import Identity
from src.models.inventory import Container, Item
from src.models.world import Node
from src.units import amount, amount_float, money


async def test_an_owner_pays_the_debt_while_the_tick_stands_the_cut_off_machine(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The tick asks a machine's meter whether the node is cut off (D-149) and
    keeps its transaction open for the rest of the world's factories; what this
    pins is that the reading leaves no lock on the meter's row behind. The owner
    paying the debt meanwhile -- locking the purse, then writing the meter --
    walks straight through instead of waiting for the step to end, and so does
    a hand on the floor's canister: the machine the debt stands locks no vessel.
    A meter held that long would put it on the tick's lock order, ahead of the
    purses the step reaches for last (`bill.pay`), where a payer holding a purse
    and wanting the meter is that order the other way round."""
    node, yard, identity, body, machine = await _factory_floor(session, constants)
    node.owner_identity_id = identity.id
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    lube = await _lube_in(session, yard, 100)
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)
    meter = await utility.meter_of(session, node)
    assert meter is not None
    meter.cut_off, meter.debt = True, money(1)
    moment = row.counted_at + timedelta(hours=2)
    inside = await session.get(Container, lube.container_id)
    assert inside is not None
    ids = (row.id, meter.id, node.id, identity.id, inside.owner_id, lube.id)
    await session.commit()
    row_id, meter_id, node_id, identity_id, canister_id, lube_id = ids

    async with factory() as tick, tick.begin():
        assert await automat.tick_automats(tick, constants, now=moment) == 0
        async with factory() as owner, owner.begin():
            #: A tick holding the meter, or the floor's canister and the
            #: lubricant in it, makes this fail at once, not hang.
            await owner.execute(text("SET LOCAL lock_timeout = '2s'"))
            assert await owner.get(Item, canister_id, with_for_update=True) is not None
            assert await owner.get(Item, lube_id, with_for_update=True) is not None
            payer = await owner.get(Identity, identity_id)
            where = await owner.get(Node, node_id)
            assert payer is not None and where is not None
            assert await utility.pay(owner, constants, payer, where) == money(1)

    async with factory() as db:
        paid = await db.get(UtilityMeter, meter_id)
        stood = await db.get(AutomatRow, row_id)
        assert paid is not None and not paid.cut_off and paid.debt == 0
        assert stood is not None and stood.counted_at == moment


async def test_a_hand_pours_from_the_frozen_floors_canister_while_the_tick_stands_it(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The cold stands a machine (D-231) before it takes anything of its yard,
    and warmth is read, never locked -- the yard, the neighbours, the pool. The
    tick keeps its transaction open for the rest of the world's factories, and a
    hand taking the frozen floor's canister, the lubricant in it and then the
    pool -- a pour's order, then a bench's -- walks straight through. A machine
    that locked its yard's vessels or stacks before asking would hold them to the
    end of the step; a warmth that locked the pool would put it ahead of the
    stacks a later machine of the pass takes."""
    node, yard, identity, body, machine = await _factory_floor(session, constants)
    await _on_aurora(session, node, await session.get(Node, node.parent_id))
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="test")
    lube = await _lube_in(session, yard, 100)
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)
    moment = row.counted_at + timedelta(hours=2)
    inside = await session.get(Container, lube.container_id)
    assert inside is not None
    ids = (row.id, lube.id, inside.owner_id, node.parent_id)
    await session.commit()
    row_id, lube_id, canister_id, city_id = ids

    async with factory() as tick, tick.begin():
        assert await automat.tick_automats(tick, constants, now=moment) == 0
        async with factory() as hand, hand.begin():
            #: A tick holding any of them makes this fail at once, not hang.
            await hand.execute(text("SET LOCAL lock_timeout = '2s'"))
            assert await hand.get(Item, canister_id, with_for_update=True) is not None
            taken = await hand.get(Item, lube_id, with_for_update=True)
            assert taken is not None
            taken.amount -= amount(1)
            pool = select(EnergyPool).where(EnergyPool.node_id == city_id).with_for_update()
            assert (await hand.execute(pool)).scalar_one() is not None

    async with factory() as db:
        left = await db.get(Item, lube_id)
        stood = await db.get(AutomatRow, row_id)
        assert left is not None and amount_float(left.amount) == pytest.approx(99)
        assert stood is not None and stood.counted_at == moment
