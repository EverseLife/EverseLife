# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""One door to `Node.properties`, and a lock behind it.

The column is a single JSONB dict, and SQLAlchemy only notices an assignment
of a whole new dict -- a key set in place is a mutation inside the column and
goes unseen. So every writer rebuilds the dict. Built from a value read
*before* the row is held, that dict is a snapshot: two transactions in the
same second both rewrite the whole map, and the later one silently erases the
earlier one's key -- a founder's gate mark wipes a scout's counter, or the
other way round (review of D-238).

So the properties map is on the same list as money and remainders (CLAUDE.md):
it changes only under the row's lock. Both helpers reread the column
`FOR UPDATE` before merging, so the merge starts from what is actually in the
base, and the lock is held to the commit -- the next writer waits and starts
from this one's result. Readers keep reading without a lock: a snapshot is
fine to look at, only to write from.

The one honest bypass is `world.create_node`: the dict given at birth goes in
with the INSERT, and nobody can hold a row that is not yet committed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.world import Node


async def _held(session: AsyncSession, node: Node) -> dict[str, Any]:
    """The node's properties as the base has them right now, row held.

    The explicit flush first: `refresh` reloads the named attributes from the
    base and would silently discard a pending change on them; and a node made
    in this very transaction (`places.assign` under `create_node`) must reach
    the base before its row can be locked at all.
    """
    #: A pending rewrite of the very column would go to the base unmerged by
    #: the flush below -- the exact lost update this module exists to prevent.
    #: A caller holding one has bypassed the helper; refuse loudly. A node not
    #: yet inserted is exempt: its dict rides the INSERT and erases nobody.
    state = inspect(node)
    if state.persistent and state.attrs.properties.history.has_changes():
        raise RuntimeError("node.properties changed outside props.stamp/bump; merge through them")
    await session.flush()
    await session.refresh(node, ["properties"], with_for_update=True)
    return dict(node.properties or {})


async def stamp(session: AsyncSession, node: Node, changes: Mapping[str, Any]) -> dict[str, Any]:
    """Merge `changes` into the node's properties under the row lock."""
    node.properties = {**(await _held(session, node)), **changes}
    await session.flush()
    return node.properties


async def bump(session: AsyncSession, node: Node, key: str, by: int = 1) -> int:
    """Add `by` to an integer property under the row lock; the new value back."""
    fresh = await _held(session, node)
    value = int(fresh.get(key, 0)) + by
    node.properties = {**fresh, key: value}
    await session.flush()
    return value
