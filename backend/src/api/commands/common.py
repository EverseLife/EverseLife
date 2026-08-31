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

from src import i18n
from src.api.registry import Refused
from src.constants import current_catalog, current_renames
from src.engine.world import body_container
from src.models.identity import Body, BodyState, Identity
from src.models.inventory import Item
from src.models.world import Node


def speaks(state: dict) -> str:
    """The language this session is answered in (D-251 wave III).

    Read through here rather than off the dict: a socket session always has
    one, but a handler called from a test or a job may be handed a state that
    was built by hand, and a missing language must give the default rather
    than an exception in the middle of an answer.
    """
    return i18n.normalize(state.get("locale"))


def goods_key(value: object) -> str:
    """Inbound goods spelling to its D-251 id.

    The wire speaks ids, but a tab opened before the release still sends
    Russian names -- `resolve()` carries both onto the id, and the engine
    below never queries the database by a spelling.
    """
    return current_catalog().recipes.resolve(str(value))


def tier_key(value: object) -> str | None:
    """Inbound quality-tier spelling to its id, same transition as goods_key."""
    if value is None:
        return None
    return current_renames().tiers.get(str(value), str(value))


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
        raise Refused(key="cmd-no-live-body")
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
        raise Refused(key="cmd-no-live-body")
    return body


async def _own_item(db: AsyncSession, body: Body, item_id: str) -> Item:
    """A thing in the hands. You repair and take apart your own, not what lies nearby."""

    item = await db.get(Item, uuid.UUID(item_id))
    inventory = await body_container(db, body)
    if item is None or item.container_id != inventory.id:
        raise Refused(key="cmd-item-not-yours")
    return item


async def _identity(state: dict, db: AsyncSession) -> Identity:
    """The identity. It is controlled remotely -- also when the body is dead."""
    identity = await db.get(Identity, state["identity_id"])
    if identity is None:  # pragma: no cover
        raise Refused(key="cmd-identity-gone")
    return identity


async def _node(db: AsyncSession, key: str) -> Node:
    """A node by stable key: orders are managed from anywhere."""
    node = (await db.execute(select(Node).where(Node.key == key))).scalar_one_or_none()
    if node is None:
        raise Refused(key="cmd-no-such-node", node=key)
    return node
