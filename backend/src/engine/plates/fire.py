# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The fire: what lies under the open sky dies with the ground it lies on.

The one door matter leaves the world by during an eruption. The rift under a
walker leaves by the same one (`ways._kill_on`), and so does the rest of what
the world ends whole: it is `world.destroy`, and this room only decides what
in a shaken node is lying there to burn.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import current_catalog
from src.engine import storage, world
from src.models.inventory import Item
from src.models.world import Node


async def _burn(session: AsyncSession, shaken: list[Node]) -> float:
    """What lies under the open sky burns with the ground (D-197).

    There is no warehouse in the fields, and that is the point: hauling is
    always part of the work here, and the logistics of Pyroxis are dear by the
    world's build rather than by anybody's tariff.

    Taken under a lock, and re-read after it: somebody carrying a sack out of
    the node in the last minute of the window is doing exactly what the window
    is for, and their sack must not be burned out of their hands.

    The vessels are locked before the rest, each part in id order: a pour
    takes its canisters before the liquid in them, and the automat family's
    tick takes a yard's vessels before the machine it wears and the stacks it
    draws (`liquid.lock_vessels`, `automat.run`). One id order over all of them
    held a sack on the floor while it waited for a canister that tick held --
    the tick waiting on that sack. What this still leaves to the worker's retry
    is written down with the rest of the package's lock order (`clock.py`).

    **A cart burns with its load, and its harness lets go.** A cart stands in
    the node like a machine (D-157), its hold is its own and goes where the
    cart goes (D-313), so the load goes into the fire with it; the carter
    beside it stays standing, as everybody in a burning field does. A harness
    is no roof: were a pulled cart spared, staying harnessed would make it the
    eternal warehouse in the fields the decision rules out. Nor is a cart
    whose carter has set out spared -- the convoy has not left until its leg
    arrives (D-194), and the window is hours wide where a leg is minutes.
    """
    yards = [(await world.node_container(session, node)).id for node in shaken]
    catalog = current_catalog()
    vessels = sorted(key for key in catalog.recipes.names() if storage.is_vessel(catalog, key))
    here_now = Item.container_id.in_(yards)
    lying = [
        *await _lock(session, here_now, Item.type_key.in_(vessels)),
        *await _lock(session, here_now, Item.type_key.not_in(vessels)),
    ]
    #: Only what is still here: somebody carrying a sack out in the last minute
    #: of the window is doing exactly what the window is for.
    here = [thing for thing in lying if thing.container_id in yards]
    return sum((await world.destroy(session, here)).values())


async def _lock(session: AsyncSession, *where: ColumnElement[bool]) -> Sequence[Item]:
    """The things matching `where`, locked in id order and reread under the lock."""
    return (
        (
            await session.execute(
                select(Item)
                .where(*where)
                .order_by(Item.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
