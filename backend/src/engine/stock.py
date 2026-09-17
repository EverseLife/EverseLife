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

from src.constants import current_catalog
from src.models.inventory import INSIDE_KINDS, Container, Item


async def locked_stacks(
    session: AsyncSession,
    container_id: uuid.UUID | Sequence[uuid.UUID],
    type_keys: Iterable[str],
    *,
    worst_first: bool = False,
    barred: tuple[uuid.UUID, Iterable[str]] | None = None,
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

    `barred` keeps some of the names out of one of those containers -- the
    fuel of a plant's pile, kept from the hand, a work and an automat (D-189,
    D-315, D-342) -- in the same query: a stack the consumer may not have is
    never locked.

    **A thing holding something is not among them** (`holding`): the stacks
    come back to be spent, and a pot spent with water in it takes the water out
    of the world. It is dropped after the lock, not before, and stays locked --
    the answer must not change between here and the write-off.
    """
    within = [container_id] if isinstance(container_id, uuid.UUID) else list(container_id)
    stmt = select(Item).where(Item.container_id.in_(within), Item.type_key.in_(tuple(type_keys)))
    if barred is not None:
        pile, names = barred
        stmt = stmt.where(~((Item.container_id == pile) & Item.type_key.in_(tuple(names))))
    if worst_first:
        stmt = stmt.order_by(Item.quality.asc().nulls_first(), Item.id)
    else:
        stmt = stmt.order_by(Item.id)
    #: `populate_existing`: a stack read earlier in the same command (the
    #: tick counts the coal before it burns it) is reread after the lock,
    #: or the decrement would be written from the value before it.
    stmt = stmt.with_for_update().execution_options(populate_existing=True)
    rows = list((await session.execute(stmt)).scalars().all())
    held = await holding(session, rows)
    return [row for row in rows if row.id not in held]


async def holding(session: AsyncSession, stacks: Sequence[Item]) -> frozenset[uuid.UUID]:
    """Those of the stacks that have something inside them (D-344).

    What a storage holds lies in a container of its own, and so does a
    vehicle's cargo -- tied to the thing by id and not by a foreign key. A
    write-off deletes the row alone, so a chest, a pot or a barrow spent with
    something in it leaves that something in a place that no longer exists:
    the water in the only pot of a battery left the world without a word. So a
    thing holding anything is not material -- no work takes it as an input and
    no machine does, as a full chest is not taken down (D-181). Emptied first,
    it is spent like any other thing.

    Asked of rows the caller has **locked**, where it writes them off: the
    doors that put something into a storage take its row first
    (`storage._allowed`, `liquid._lock`), so an answer read under that lock
    holds until the write-off, and a pour waiting on it finds the vessel gone
    and pours nowhere. Read before the lock, a pour committing in between would
    be missed. Loading a hold takes the vehicle's row the same way
    (`transport._pulled_here`), so that door is no exception either.

    No query at all unless one of the names can hold anything.
    """
    book = current_catalog().recipes
    boxes = {key for key in {stack.type_key for stack in stacks} if book.has_inside(key)}
    if not boxes:
        return frozenset()
    found = await session.execute(
        select(Container.owner_id).where(
            Container.kind.in_(INSIDE_KINDS),
            Container.owner_id.in_([stack.id for stack in stacks if stack.type_key in boxes]),
            select(Item.id).where(Item.container_id == Container.id).exists(),
        )
    )
    return frozenset(found.scalars().all())


async def lock_items(
    session: AsyncSession, items: Sequence[Item], *, ordered: bool = False
) -> list[Item]:
    """The same items, locked and reread, in id order. For consumers that
    gathered their stacks from several containers (a ship's rooms).

    `ordered` hands them back in the order they were given instead: a line
    drinks its vessels in the owner's order (D-288), and the lock -- always
    taken in id order, so two consumers never deadlock -- must not reshuffle
    it. A stack gone between the gathering and the lock is simply absent.
    """
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
    if not ordered:
        return list(rows)
    by_id = {row.id: row for row in rows}
    return [by_id[item.id] for item in items if item.id in by_id]


async def consume(session: AsyncSession, stacks: Sequence[Item], quantity: int) -> int:
    """Take `quantity` (in amount units) from locked stacks in order, deleting
    what runs empty. Returns what was actually taken -- less than asked when
    the stacks run out. The caller decides whether that is a refusal.

    The row alone is deleted, never what lies inside it: a thing holding
    something must not reach this door, and the gathering doors keep it out
    (`locked_stacks`, `craft._stock`, both through `holding`). Skipping it here
    instead would be worse -- the caller has already counted it, and a battery
    would come out of a pot nobody spent."""
    #: A relic of the Forerunners is not spent (D-232). The guard stands here
    #: rather than at each caller because "consume" is the one door every
    #: write-off goes through: a recipe naming a relic class, a station burning
    #: its fuel, a hopper emptying itself -- all of them end up here.
    book = current_catalog().recipes
    left = quantity
    for stack in stacks:
        if left <= 0:
            break
        if book.is_relic(stack.type_key):
            continue
        take = min(left, stack.amount)
        if take == stack.amount:
            await session.delete(stack)
        else:
            stack.amount -= take
        left -= take
    await session.flush()
    return quantity - left
