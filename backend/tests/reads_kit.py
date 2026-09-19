# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The shared ground of the "reads write nothing" family: the guard that fails
on any write of a session, and the master whose world every forecast has
something to answer about.

Used by `test_reads.py` and `test_reads_forecast.py`; not collected by pytest.
No real fixture lives here on purpose -- a `@pytest.fixture` in a kit is
imported for its name alone, ruff removes the import as unused, and pytest
then cannot find it.
"""

from __future__ import annotations

import uuid
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import current, current_catalog
from src.db.readonly import writes_forbidden
from src.engine import transport, world


def _writes_forbidden(db: AsyncSession, what: str = "read") -> AbstractAsyncContextManager[None]:
    """Fail on any write of this session -- the only honest way to say "wrote
    nothing".

    `db.new` / `db.dirty` / `db.deleted` are empty **after** a flush: the row
    is persistent by then and the session is clean again. So the very shape
    the yard used to be created in -- `session.add` followed by
    `await session.flush()` -- passes those three checks in silence. The
    listener sees the flush itself, whoever caused it.

    The listener moved to `db/readonly.py` on 2026-09-04, where every command
    declaring `readonly=True` now runs under it: the tests here hold engine
    calls to the same rule from below, one path at a time, and go on doing so
    whatever a copy's `EVERSELIFE_READONLY_GUARD` says -- hence the mode named
    outright.
    """
    return writes_forbidden(db, what, mode="raise")


async def _forecaster(session: AsyncSession, name: str) -> uuid.UUID:
    """A master on an empty plot with a forge in the yard: both forecasts have
    everything they need -- a bill to count and a batch to price.

    **Harnessed to an empty wagon**, and that is not decoration. A hold is made
    on first need, and `harness` does not make one -- so a body pulling nothing
    is exactly the world in which a read can furnish a hold from a glance. The
    reach of a work walks the hold at every forecast (D-315) and the window
    lists its cargo at every `look`: with no such body among these reads the
    whole family went unswept, and the leak was found by a reviewer rather than
    here.
    """
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.{name}.{stamp}", "Мастерская", area_m2=100)
    identity = await world.create_identity(session, f"Зодчий-{stamp}")
    body = await world.print_body(session, identity, node)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "forge", quality=60, origin="тест")
    cart = await world.grant_item(session, yard, "cart", amount=1, origin="тест")
    await transport.harness(session, current(), current_catalog(), body, cart)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "iron_ingot", amount=10, quality=80, origin="тест")
    await world.learn(session, identity, "nails")
    await session.commit()
    return identity.id
