# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Shared prologues of the socket commands: who is asking, their body, their node.

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.registry import Refused
from src.engine.world import body_container
from src.models.identity import Body, BodyState, Identity
from src.models.inventory import Item
from src.models.world import Node


def _stamp(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


async def _body(db: AsyncSession, identity_id: uuid.UUID) -> Body | None:
    stmt = select(Body).where(Body.identity_id == identity_id, Body.state == BodyState.ALIVE)
    return (await db.execute(stmt)).scalars().first()


async def _alive(state: dict, db: AsyncSession) -> Body:
    """The body being acted with. Matter requires presence (D-044).

    The body row is **locked** for the command: one body does one thing at a
    time (D-211), and the lock is what makes that true under two sockets of
    one identity or an action racing the worker. Everything in the pocket,
    the stamina and the occupation are then changed by one transaction at a
    time. Reads (`look`, the forecasts) go through `_alive_read` and lock
    nothing.
    """
    stmt = (
        select(Body)
        .where(Body.identity_id == state["identity_id"], Body.state == BodyState.ALIVE)
        .with_for_update()
        #: A body already in the identity map is reread after the lock, not
        #: served from before it.
        .execution_options(populate_existing=True)
    )
    body = (await db.execute(stmt)).scalars().first()
    if body is None:
        raise Refused("нет живого тела")
    return body


async def _alive_read(state: dict, db: AsyncSession) -> Body:
    """The body a read is answered about. Same refusal as `_alive`, no lock.

    A forecast changes nothing, so it has nothing to serialise against: taking
    `FOR UPDATE` for it puts the whole of `craft.plan` and `build.estimate` in
    the queue behind every action of the same body -- and the client counts
    them while the player is still typing, at three a second. A read that waits
    for a lock it does not need also holds it against the worker's tick, which
    is the wrong way round: reading must never be able to delay writing.

    The answer is the world as it was read, statement by statement -- the
    transaction is READ COMMITTED, so a forecast may well count a pocket from
    before somebody else's commit and a yard from after it. That is what a
    forecast is: the batch it precedes is priced again under `_alive`, by the
    same code (`craft._prepare` runs for both, D-092).
    """
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused("нет живого тела")
    return body


async def _own_item(db: AsyncSession, body: Body, item_id: str) -> Item:
    """A thing in the hands. You repair and take apart your own, not what lies nearby."""

    item = await db.get(Item, uuid.UUID(item_id))
    inventory = await body_container(db, body)
    if item is None or item.container_id != inventory.id:
        raise Refused("этой вещи у вас нет")
    return item


async def _identity(state: dict, db: AsyncSession) -> Identity:
    """The identity. It is controlled remotely -- also when the body is dead."""
    identity = await db.get(Identity, state["identity_id"])
    if identity is None:  # pragma: no cover
        raise Refused("личность исчезла")
    return identity


async def _node(db: AsyncSession, key: str) -> Node:
    """A node by stable key: orders are managed from anywhere."""
    node = (await db.execute(select(Node).where(Node.key == key))).scalar_one_or_none()
    if node is None:
        raise Refused(f"нет узла {key!r}")
    return node
