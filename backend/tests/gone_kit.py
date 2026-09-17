# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The sides that go first in the races over a thing gone from under a reaching hand.

A lift that takes a thing into the hands and a fire that burns a yard
(`plates._burn`), each holding the rows it took until the other side provably
waits on one of them (`conftest._until_blocked_by`). Shared by the floor's
races (`test_races_gone.py`), the harness's (`test_races_harness.py`), the
charging counter's (`test_races_energy.py`) and the fire's own
(`test_races_fire.py`) -- the family's own pattern, see `mining_kit.py`.

Pytest does not collect this file: it holds no tests and no fixtures.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _until_blocked_by
from src.constants import Catalog, Constants
from src.engine import plates, storage
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node


async def _lifting(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    lifter_id: uuid.UUID,
    lifted_id: uuid.UUID,
    *,
    held: asyncio.Event | None = None,
) -> float:
    """Pick the thing up off the floor. Given `held`, the lift that goes first:
    its row is held until the other side provably waits on it."""
    async with factory() as db, db.begin():
        me = await db.get(Body, lifter_id)
        thing = await db.get(Item, lifted_id)
        assert me is not None and thing is not None
        taken = await storage.pick(db, constants, catalog, me, thing)
        if held is not None:
            held.set()
            await _until_blocked_by(factory, db)
        return taken


async def _burning(
    factory: async_sessionmaker[AsyncSession], node_id: uuid.UUID, held: asyncio.Event
) -> float:
    """The fire that goes first: the yard burnt (`plates._burn`), and the
    rows it took held until the other side provably waits on one of them."""
    async with factory() as db, db.begin():
        spot = await db.get(Node, node_id)
        assert spot is not None
        burnt = await plates._burn(db, [spot])
        held.set()
        await _until_blocked_by(factory, db)
        return burnt
