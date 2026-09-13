# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the automat tests build a factory floor out of.

The floor -- a city yard with a machine, a pool and a funded owner -- and the
lubricant canister are shared by `test_automat.py`, `test_fuel_plant.py` and
the race files, `test_races_automat.py`, `test_races_energy.py` and
`test_races_liquid.py`; so are the races' handshake and their reading of a
pool, which the meter's races (`test_races_meter.py`) take as well -- and the
handshake alone, the races over a thing gone from under a reaching hand
(`test_races_gone.py`). That is why
they are here and not beside one of them (the family's own pattern, see
`mining_kit.py`).

Pytest does not collect this file: it holds no tests and no fixtures -- a
real `@pytest.fixture` must not live here, because the import that puts its
name in a signature reads as unused to ruff and pytest never finds it.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Constants
from src.engine import energy, ledger, storage, world
from src.models.identity import Identity
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node
from src.units import money

NAILS = "nails"
IRON = "iron_ingot"
LUBRICANT = "lubricant"


async def _factory_floor(
    session: AsyncSession,
    constants: Constants,
    *,
    machine_kind: str = "auto_station",
    stored_energy: float = 10_000,
    funded: bool = True,
):
    """A city yard with an automat standing in it, a pool, and an owner who may build."""
    stamp = uuid.uuid4().hex[:8]
    capital = await world.create_node(
        session, f"terra.fab.{stamp}", "Capital", area_m2=1, layer=Layer.PLANET
    )
    yard_node = await world.create_node(
        session,
        f"terra.fab.{stamp}.floor",
        "Floor",
        area_m2=200,
        layer=Layer.PLANET,
        parent=capital,
    )
    identity = await world.create_identity(session, f"Maker-{stamp}")
    body = await world.print_body(session, identity, yard_node)
    yard = await world.node_container(session, yard_node)
    machine = await world.grant_item(session, yard, machine_kind, quality=70, origin="test")
    pool = await energy.pool_of(session, constants, yard_node)
    assert pool is not None
    pool.stored = Decimal(str(stored_energy))
    #: The owner can pay the energy bill: an unpaid one stops the machine
    #: (D-135), and that is its own test, not every test's noise.
    if funded:
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=account.id,
            amount=money(100_000),
            memo={},
        )
    await session.flush()
    return yard_node, yard, identity, body, machine


async def _lube_in(session: AsyncSession, yard, units: float) -> Item:
    """Lubricant standing in the node: a canister with the liquid inside (D-230)."""
    canister = await world.grant_item(session, yard, "canister", quality=60, origin="test")
    inside = await storage.inside(session, canister)
    return await world.grant_item(
        session, inside, LUBRICANT, amount=units, quality=55, origin="test"
    )


async def _learn(session: AsyncSession, identity, key: str) -> None:
    row = await session.get(Identity, identity.id)
    await world.learn(session, row, key)


_BLOCKED = text("SELECT count(*) FROM pg_stat_activity WHERE :holder = ANY(pg_blocking_pids(pid))")


async def _until_blocked_by(
    factory: async_sessionmaker[AsyncSession],
    holder: AsyncSession,
    *,
    unless: asyncio.Future | None = None,
) -> bool:
    """Return once another transaction waits on a lock `holder` holds: `True`.

    A fixed pause would let a busy run release the held rows before the other
    side reached them, and the race would pass on the very code it exists to
    catch. Asked by the holder's own backend, so no unrelated wait in the
    database counts; the activity view is a snapshot per transaction, so each
    look is a transaction of its own.

    `unless` is the other side's task, for a race about whether that side
    waits at all: on the code it catches, the other side walks straight
    through and finishes -- `False` then, and the test fails on what it did
    rather than on a handshake that never came.
    """
    pid = (await holder.execute(text("SELECT pg_backend_pid()"))).scalar_one()
    async with factory() as probe:
        for _ in range(500):
            blocked = (await probe.execute(_BLOCKED, {"holder": pid})).scalar_one()
            await probe.rollback()
            if blocked:
                return True
            if unless is not None and unless.done():
                return False
            await asyncio.sleep(0.01)
    raise AssertionError("nobody came to wait on the held rows")


async def _pool_left(factory: async_sessionmaker[AsyncSession], constants, node_id) -> float:
    async with factory() as db:
        node = await db.get(Node, node_id)
        assert node is not None
        pool = await energy.pool_of(db, constants, node, create=False)
        assert pool is not None
        return float(pool.stored)
