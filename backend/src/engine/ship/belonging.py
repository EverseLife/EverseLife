# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: who belongs to what.

Split out of `engine/ship.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine.ship._base import ABOARD
from src.models.identity import Body, BodyState
from src.models.ship import Ship
from src.models.world import Node


def is_aboard(node: Node) -> bool:
    """Whether this node is part of a ship. Land is land."""
    return bool((node.properties or {}).get(ABOARD))


async def of_node(session: AsyncSession, node: Node) -> Ship | None:
    """Which ship this node belongs to -- or none, if it is ground.

    Membership is the `parent` hierarchy, the same one a city has over its
    locations (D-097): no second way to say "this node is part of that group".
    """
    if not is_aboard(node) or node.parent_id is None:
        return None
    return (
        (await session.execute(select(Ship).where(Ship.node_id == node.parent_id)))
        .scalars()
        .first()
    )


async def nodes_of(session: AsyncSession, ship: Ship) -> list[Node]:
    """The nodes aboard: children of the group's delegate node."""
    return list(
        (
            await session.execute(
                select(Node).where(Node.parent_id == ship.node_id).order_by(Node.created_at)
            )
        )
        .scalars()
        .all()
    )


async def ships_of(session: AsyncSession, identity_id: uuid.UUID) -> list[Ship]:
    """Whose ships these are. Ownership is personal: nodes aboard bear no title (D-198)."""
    return list(
        (
            await session.execute(
                select(Ship).where(Ship.owner_identity_id == identity_id).order_by(Ship.created_at)
            )
        )
        .scalars()
        .all()
    )


async def aboard_of(session: AsyncSession, body: Body) -> Ship | None:
    """The ship the body is standing in, if it is standing in one at all."""
    node = await session.get(Node, body.node_id)
    return None if node is None else await of_node(session, node)


async def crew_of(session: AsyncSession, ship: Ship) -> list[Body]:
    """Living bodies aboard. A guest counts as crew: life support does not ask for a pass."""
    nodes = await nodes_of(session, ship)
    if not nodes:  # pragma: no cover -- a ship always has its connector
        return []
    return list(
        (
            await session.execute(
                select(Body).where(
                    Body.node_id.in_([node.id for node in nodes]),
                    Body.state == BodyState.ALIVE,
                )
            )
        )
        .scalars()
        .all()
    )
