# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Stacks of a shared store, locked and consumed.

The tick burning coal in the yard, a build taking timber, a ship spending its
foundation: every consumer of a shared container reads stacks and then
decrements them. Without the lock the worker and a player carrying the same
stack away write over each other (review 2026-08-23). One place for the
lock and the write-off, instead of six copies.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.inventory import Item


async def locked_stacks(
    session: AsyncSession,
    container_id: uuid.UUID | Sequence[uuid.UUID],
    type_keys: Iterable[str],
    *,
    worst_first: bool = False,
) -> list[Item]:
    """Stacks of the named goods in a container, **locked** for the transaction.

    Every consumer of a shared store -- the tick burning coal in the yard,
    a build taking timber, a ship spending its foundation -- reads stacks
    and then decrements them. Without the lock the worker and a player
    carrying the same stack away write over each other (review 2026-08-23,
    wave 2). Order by id so two consumers of one yard never deadlock;
    `worst_first` puts the lowest quality first for write-offs. Several
    containers at once -- a pocket and the canisters in it (D-230) -- are one
    query and one lock order, never two.
    """
    within = [container_id] if isinstance(container_id, uuid.UUID) else list(container_id)
    stmt = select(Item).where(Item.container_id.in_(within), Item.type_key.in_(tuple(type_keys)))
    if worst_first:
        stmt = stmt.order_by(Item.quality.asc().nulls_first(), Item.id)
    else:
        stmt = stmt.order_by(Item.id)
    #: `populate_existing`: a stack read earlier in the same command (the
    #: tick counts the coal before it burns it) is reread after the lock,
    #: or the decrement would be written from the value before it.
    stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return list((await session.execute(stmt)).scalars().all())


async def lock_items(session: AsyncSession, items: Sequence[Item]) -> list[Item]:
    """The same items, locked and reread, in id order. For consumers that
    gathered their stacks from several containers (a ship's rooms)."""
    if not items:
        return []
    ids = sorted(item.id for item in items)
    rows = (
        (
            await session.execute(
                select(Item)
                .where(Item.id.in_(ids))
                .order_by(Item.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def consume(session: AsyncSession, stacks: Sequence[Item], quantity: int) -> int:
    """Take `quantity` (in amount units) from locked stacks in order, deleting
    what runs empty. Returns what was actually taken -- less than asked when
    the stacks run out. The caller decides whether that is a refusal."""
    left = quantity
    for stack in stacks:
        if left <= 0:
            break
        take = min(left, stack.amount)
        if take == stack.amount:
            await session.delete(stack)
        else:
            stack.amount -= take
        left -= take
    await session.flush()
    return quantity - left
