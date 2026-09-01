# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The wires between machines (D-253): linked, unlinked, dropped with the
machine, and ordered so a chain advances upstream first.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import events
from src.engine.automat._base import SelfLink, _machine_here
from src.models.automat import Automat as AutomatRow
from src.models.automat import AutomatLink
from src.models.event import EventKind
from src.models.identity import Body
from src.models.inventory import Item


def _chain_order(rows: list[AutomatRow], links: list[AutomatLink]) -> list[AutomatRow]:
    """Kahn over the wires, per the whole world at once: feeders first.

    Wires are keyed by the machine items; a wire whose end has no working
    row feeds nobody and is skipped. Ties and cycles keep the incoming
    order -- the input arrives sorted by row id, and the queue preserves it.
    """
    by_item = {row.item_id: row for row in rows}
    feeds: dict[object, list[object]] = {}
    waits: dict[object, int] = {row.item_id: 0 for row in rows}
    for wire in links:
        if wire.from_item_id in by_item and wire.to_item_id in by_item:
            feeds.setdefault(wire.from_item_id, []).append(wire.to_item_id)
            waits[wire.to_item_id] += 1
    queue = [row.item_id for row in rows if waits[row.item_id] == 0]
    ordered: list[AutomatRow] = []
    while queue:
        current = queue.pop(0)
        ordered.append(by_item[current])
        for fed in feeds.get(current, []):
            waits[fed] -= 1
            if waits[fed] == 0:
                queue.append(fed)
    #: A cycle of wires: whatever Kahn could not release goes in id order.
    if len(ordered) < len(rows):
        left = {row.item_id for row in ordered}
        ordered.extend(row for row in rows if row.item_id not in left)
    return ordered


async def link(
    session: AsyncSession,
    body: Body,
    from_item: Item,
    to_item: Item,
) -> AutomatLink:
    """Wire A's output to B's input. Both machines here, both this owner's ground.

    Idempotent: the same wire drawn twice is one wire. The wire's mechanical
    meaning is the tick's order; the rest is the picture the editor draws.
    """
    if from_item.id == to_item.id:
        raise SelfLink(key="auto-link-self", goods=from_item.type_key)
    node = await _machine_here(session, body, from_item)
    await _machine_here(session, body, to_item)
    #: Idempotent under a race too: two hands drawing one wire must both
    #: succeed, not one of them crash on the unique pair (the quality bar).
    await session.execute(
        pg_insert(AutomatLink)
        .values(from_item_id=from_item.id, to_item_id=to_item.id)
        .on_conflict_do_nothing(constraint="uq_automat_link")
    )
    wire = (
        await session.execute(
            select(AutomatLink).where(
                AutomatLink.from_item_id == from_item.id,
                AutomatLink.to_item_id == to_item.id,
            )
        )
    ).scalar_one()
    await events.record(
        session,
        EventKind.AUTOMAT_LINKED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        source=str(from_item.id),
        target=str(to_item.id),
    )
    return wire


async def unlink(
    session: AsyncSession,
    body: Body,
    from_item: Item,
    to_item: Item,
) -> bool:
    """Cut the wire. Idempotent: cutting what is not there changes nothing."""
    node = await _machine_here(session, body, from_item)
    await _machine_here(session, body, to_item)
    wire = (
        await session.execute(
            select(AutomatLink).where(
                AutomatLink.from_item_id == from_item.id,
                AutomatLink.to_item_id == to_item.id,
            )
        )
    ).scalar_one_or_none()
    if wire is None:
        return False
    await session.delete(wire)
    await session.flush()
    await events.record(
        session,
        EventKind.AUTOMAT_UNLINKED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        source=str(from_item.id),
        target=str(to_item.id),
    )
    return True


async def _drop_wires(session: AsyncSession, item_id) -> None:
    """Cut every wire touching this machine: it moved houses (D-047)."""
    for wire in (
        (
            await session.execute(
                select(AutomatLink).where(
                    (AutomatLink.from_item_id == item_id) | (AutomatLink.to_item_id == item_id)
                )
            )
        )
        .scalars()
        .all()
    ):
        await session.delete(wire)
