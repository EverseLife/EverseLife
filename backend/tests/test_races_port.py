# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over a port's open ground.

One of the race files (see `test_races.py` for the family's method): the
pad's metres are a remainder, and the descents ordered onto it spend them
(D-319). They are counted in two parts and two statements -- the hulls
standing on the pad and the hulls on their way down (`estate.free_ground`) --
and an arrival moves a hull from the second part to the first in one commit.
Whoever spends the ground holds the plot's row for it (`estate.hold_ground`),
and so does the arrival: a hull that landed between the two counts would be
read in neither, and the last place would be given twice.

Two descents ordered in one second are `test_ship_flight.py`'s.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import _until_blocked_by
from ship_kit import _flightworthy, _in_orbit, _laid, _port, _shipwright
from src.constants import Catalog, Constants
from src.engine import jobs, ship
from src.engine.estate.building import frame
from src.models.identity import Body
from src.models.ship import Ship
from src.models.world import Node


async def _hull_over(session: AsyncSession, constants: Constants, catalog: Catalog, home: Node):
    """A flightworthy hull and its owner, hanging over the planet of `home`."""
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()
    await _in_orbit(session, constants, catalog, owner, vessel)
    return owner, vessel


async def test_a_hull_landing_between_the_two_counts_of_a_pad_is_not_missed(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pad with room for one hull, and that hull on its way down. A second
    crew orders a descent onto the pad just as the first hull sets down: the
    order counts the standing hulls before the arrival commits and the hulls
    on the way after it, and reads the pad empty.

    The arrival used to wait on the order only by accident -- its gangway's
    foreign key holds the pad `FOR KEY SHARE`, and the order held the pad `FOR
    UPDATE`. The plot is held `FOR NO KEY UPDATE` now (the rig tick re-checks
    that key under a falling house), so the arrival takes the pad's row itself."""
    async with factory() as session, session.begin():
        home = await _port(session, name="Космодром столицы")
        pad = await _port(session, name="Тесный космодром")
        first_owner, first = await _hull_over(session, constants, catalog, home)
        second_owner, second = await _hull_over(session, constants, catalog, home)
        #: Ground for exactly one hull: the yard's roof, and one hull's worth of apron.
        pad.area_m2 = 80 + await ship.hull_footprint(session, first)
        await session.flush()
        flight = await ship.land(session, constants, catalog, first_owner, first, pad)
        term = flight.run_at
        pad_id, first_id = pad.id, first.id
        second_owner_id, second_id = second_owner.id, second.id

    counted = asyncio.Event()
    waited: list[bool] = []
    tasks: dict[str, asyncio.Future] = {}
    planned = frame.planned_footprint

    async def between_the_counts(db, *args, **kwargs):
        #: The standing hulls are counted, the hulls on the way are not yet:
        #: the arrival is let in here.
        footprint = await planned(db, *args, **kwargs)
        if not counted.is_set():
            counted.set()
            waited.append(await _until_blocked_by(factory, db, unless=tasks["arrive"]))
        return footprint

    monkeypatch.setattr(frame, "planned_footprint", between_the_counts)

    async def order() -> str:
        async with factory() as db, db.begin():
            me = await db.get(Body, second_owner_id)
            mine = await db.get(Ship, second_id)
            target = await db.get(Node, pad_id)
            try:
                await ship.land(db, constants, catalog, me, mine, target)
            except ship.NoPort as refusal:
                return refusal.key
            return "descends"

    async def arrive() -> None:
        await asyncio.wait_for(counted.wait(), timeout=30)
        assert await jobs.run_one(factory, now=term) is not None

    tasks["order"] = asyncio.ensure_future(order())
    tasks["arrive"] = asyncio.ensure_future(arrive())
    outcome = await asyncio.gather(tasks["order"], tasks["arrive"], return_exceptions=True)

    assert outcome == ["ship-no-room", None], outcome
    assert waited == [True], "посадка не встала за приказом, который держит площадку"
    async with factory() as session:
        landed = await session.get(Ship, first_id)
        assert landed is not None and landed.docked_node_id == pad_id, "первый корпус сел"
