# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over a city's line (D-356).

One of the race files (see `test_races.py` for the family's method): here the
contested thing is a node the city holds by its line -- the line taking and
letting go, the purchase, the allotment. The line never waits for a row, and
the allotment and the purchase take the row before they ask about it: the
invariants must hold whichever side wins, and neither side may die of a
deadlock.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from city_line_kit import _head, _node, _town
from conftest import _hold_the_first, _until_blocked_by
from estate_kit import _buyer
from src.constants import Catalog, Constants, current, current_catalog
from src.engine import city as town
from src.engine import estate, world
from src.engine.city import line as line_module
from src.models.city import City
from src.models.estate import Deed
from src.models.identity import Body, Identity
from src.models.world import COVERED, PLOT, Node


async def test_the_line_passes_over_a_row_somebody_holds(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The line is asked from inside a highway, a purchase, a scout's return
    and the tick, whatever those already hold -- so it never waits for a row.

    A row another transaction holds is passed over and taken up by the next
    asking. Were the line to wait, the holder here would wait on nothing and
    the line would wait on the holder: the timeout is the deadlock it avoids.
    """
    city, *_ = await _town(session, constants, catalog)
    stray = await _node(
        session, constants, "stray", 0, -300, properties={COVERED: True, PLOT: True}
    )
    stray.owner_city_id = city.id
    city_id, stray_id = city.id, stray.id
    await session.commit()

    async with factory() as holder, holder.begin():
        await holder.execute(select(Node).where(Node.id == stray_id).with_for_update())
        async with factory() as db, db.begin():
            asked = town.cover(db, constants, await db.get(City, city_id))
            assert await asyncio.wait_for(asked, timeout=10) == (0, 0), "занятую строку обошли"

    async with factory() as db, db.begin():
        assert await town.cover(db, constants, await db.get(City, city_id)) == (0, 1)
        assert (await db.get(Node, stray_id)).owner_city_id is None


async def test_an_allotment_waits_for_a_purchase_and_refuses(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A citizen buys a covered plot while the head hands it to somebody else.

    The allotment asked about the plot before it took the row, and the
    hand-over then wrote over whatever it found: the buyer paid the treasury
    and the plot went to the one it was handed to. Taking the row first, the
    allotment waits for the purchase, reads the buyer's title and refuses.
    The purchase is held after it took the row, so the allotment reaches it.
    """
    city, home, *_ = await _town(session, constants, catalog)
    head, head_body = await _head(session, city, home)
    plot = await _node(session, constants, "plot", 10, 10)
    await town.cover(session, constants, city)
    buyer, buyer_body = await _buyer(session, plot, city=city)
    other = await world.create_identity(session, "Другой-получатель")
    ids = {
        "city": city.id,
        "plot": plot.id,
        "head": head.id,
        "head_body": head_body.id,
        "buyer_body": buyer_body.id,
        "other": other.id,
    }
    await session.commit()

    held = _hold_the_first(monkeypatch, factory, world, "hand_over")

    async def purchase() -> None:
        async with factory() as db, db.begin():
            body = await db.get(Body, ids["buyer_body"])
            node = await db.get(Node, ids["plot"])
            await estate.buy(db, current(), current_catalog(), body, node)

    async def allotment() -> None:
        await held.wait()
        async with factory() as db, db.begin():
            await town.allot(
                db,
                await db.get(Identity, ids["head"]),
                await db.get(City, ids["city"]),
                await db.get(Node, ids["plot"]),
                await db.get(Identity, ids["other"]),
                body=await db.get(Body, ids["head_body"]),
            )

    outcome = await asyncio.gather(purchase(), allotment(), return_exceptions=True)
    assert outcome[0] is None, outcome
    assert isinstance(outcome[1], town.CityError), outcome
    assert outcome[1].key == "city-land-taken"
    async with factory() as db:
        node = await db.get(Node, ids["plot"])
        assert node.owner_identity_id == buyer.id, "участок у того, кто заплатил"
        deed = (await db.execute(select(Deed).where(Deed.node_id == ids["plot"]))).scalar_one()
        assert deed.owner_identity_id == buyer.id


async def test_an_allotment_does_not_hand_out_what_the_line_lets_go(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The line lets a covered plot go while the head hands it out.

    Read before the row was taken, the plot was the city's; by the hand-over
    it was wild, and the hand-over wrote a private title onto nobody's ground
    -- a title no city gave (D-198). Taking the row first, the allotment waits
    for the line, reads the wild node and refuses. The line is held after it
    locked the plot it lets go, so the allotment reaches it.
    """
    city, home, *_ = await _town(session, constants, catalog)
    head, head_body = await _head(session, city, home)
    stray = await _node(
        session, constants, "stray", 0, -300, properties={COVERED: True, PLOT: True}
    )
    stray.owner_city_id = city.id
    other = await world.create_identity(session, "Получатель")
    ids = {
        "city": city.id,
        "stray": stray.id,
        "head": head.id,
        "head_body": head_body.id,
        "other": other.id,
    }
    await session.commit()

    held = asyncio.Event()
    locked = line_module._locked

    async def holding(db: AsyncSession, node_ids):
        rows = await locked(db, node_ids)
        if any(row.id == ids["stray"] for row in rows) and not held.is_set():
            held.set()
            await _until_blocked_by(factory, db)
        return rows

    monkeypatch.setattr(line_module, "_locked", holding)

    async def letting_go() -> tuple[int, int]:
        async with factory() as db, db.begin():
            return await town.cover(db, constants, await db.get(City, ids["city"]))

    async def allotment() -> None:
        await held.wait()
        async with factory() as db, db.begin():
            await town.allot(
                db,
                await db.get(Identity, ids["head"]),
                await db.get(City, ids["city"]),
                await db.get(Node, ids["stray"]),
                await db.get(Identity, ids["other"]),
                body=await db.get(Body, ids["head_body"]),
            )

    outcome = await asyncio.gather(letting_go(), allotment(), return_exceptions=True)
    assert outcome[0] == (0, 1), outcome
    assert isinstance(outcome[1], town.CityError), outcome
    assert outcome[1].key == "city-land-not-civic"
    async with factory() as db:
        node = await db.get(Node, ids["stray"])
        assert node.owner_identity_id is None and node.owner_city_id is None, "дикая и ничья"
