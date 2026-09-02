# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The alpha's two extra levers of 2026-09-02 (D-229 addendum): a print onto
the floor of the node, past the hands and the carry limit, and energy put
into a city pool -- the one door a dry test world needed most.

Checked is what keeps them from becoming holes: the floor print still names
its ground and its place; energy goes only into a pool that exists; and the
pool being a remainder, a print and a charge rewriting it at once lose
nothing to each other.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from src.constants import Catalog, Constants
from src.engine import alpha, battery, energy, world
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.inventory import Item
from test_alpha_kit import ORE, _body, _carried, _grid, _master


async def test_printed_onto_the_floor_lies_in_the_node(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Straight onto the floor, past the hands and the carry limit (D-265): a
    site and a station built in place need tonnes, and a tester should not
    carry them in thirty-kilogram trips."""
    node, body = await _master(session)
    made = await alpha.spawn(
        session, constants, catalog, body, type_key=ORE, amount=500, where=alpha.FLOOR
    )
    yard = await world.node_container(session, node)
    assert made.container_id == yard.id
    assert not [item for item in await _carried(session, body) if item.type_key == ORE]
    kinds = [event.kind for event in (await session.execute(select(Event))).scalars()]
    assert EventKind.ITEM_FELL.value not in kinds, "на пол кладут, а не роняют"
    asked = [
        event
        for event in (await session.execute(select(Event))).scalars()
        if event.kind == EventKind.ALPHA_SPAWNED.value
    ]
    assert asked[-1].payload["where"] == alpha.FLOOR


async def test_a_print_names_one_of_two_places(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    body = await _body(session)
    with pytest.raises(alpha.AlphaError):
        await alpha.spawn(session, constants, catalog, body, type_key=ORE, where="сундук")


async def test_energy_is_printed_into_the_city_pool(
    session: AsyncSession, constants: Constants
) -> None:
    """Into the pool and nowhere else: a battery is filled from it by the
    ordinary door, so nothing here can charge what the grid could not."""
    yard, body = await _grid(session)
    stored = await alpha.energize(session, constants, body, 300)
    pool = await energy.pool_of(session, constants, yard)
    assert pool is not None and float(pool.stored) == pytest.approx(300) == stored
    asked = [
        event
        for event in (await session.execute(select(Event))).scalars()
        if event.kind == EventKind.ALPHA_ENERGIZED.value
    ]
    assert [event.actor_identity_id for event in asked] == [body.identity_id]
    assert asked[0].payload["energy"] == 300


async def test_energy_needs_a_grid_to_print_into(
    session: AsyncSession, constants: Constants
) -> None:
    """Outside a city there is no pool, and the widget does not invent one."""
    body = await _body(session)
    with pytest.raises(alpha.AlphaError):
        await alpha.energize(session, constants, body, 100)
    _, townsman = await _grid(session)
    with pytest.raises(alpha.AlphaError):
        await alpha.energize(session, constants, townsman, 0)


async def test_printing_energy_and_charging_do_not_lose_each_other(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pool is a remainder: a print and a charge both rewrite `stored`,
    and without the row lock the later write would undo the earlier one."""
    _slow(monkeypatch, energy, "produce")
    yard, body = await _grid(session)
    pool = await energy.pool_of(session, constants, yard)
    assert pool is not None
    pool.stored = Decimal("1000")
    pool.tariff = Decimal(0)
    pool.counted_at = datetime.now(UTC)
    pocket = await world.body_container(session, body)
    cell = await world.grant_item(session, pocket, energy.BATTERY, quality=60, origin="тест")
    body_id, cell_id, yard_id = body.id, cell.id, yard.id
    await session.commit()

    async def print_energy() -> None:
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            assert me is not None
            await alpha.energize(db, constants, me, 300)

    async def charge() -> None:
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            item = await db.get(Item, cell_id)
            assert me is not None and item is not None
            await battery.charge_battery(db, constants, me, item, 100)

    await asyncio.gather(print_energy(), charge())
    async with factory() as db:
        node = await db.get(type(yard), yard_id)
        assert node is not None
        again = await energy.pool_of(db, constants, node, create=False)
        assert again is not None
        assert float(again.stored) == pytest.approx(1000 + 300 - 100, abs=0.05)
