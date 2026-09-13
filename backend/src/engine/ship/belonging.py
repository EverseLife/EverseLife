# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: who belongs to what.

Split out of `engine/ship.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import world
from src.models.identity import Body, BodyState
from src.models.ship import Ship
from src.models.world import Node
from src.models.world import is_aboard as is_aboard


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
    return await _living_in(session, {node.id for node in await nodes_of(session, ship)})


async def lock_crew(session: AsyncSession, ship: Ship) -> list[Body]:
    """The crew, their rows locked for the transaction and reread, in id order.

    For whoever is about to kill a crew: the air run out (`oxygen._breathe`),
    the hull lost (`fate._lose`). A body's row comes before what lies in its
    hands (`world.lock_bodies`), and `death.die` takes the pocket first and
    writes the body last -- so a death that did not hold the body already held
    the stack in a crew member's hands while waiting for their row, and the
    member picking a sack up (`_alive`) or being handed a parcel
    (`storage.hand`) held the row and folded the arrival into that stack.

    Reread, since the wait was for whoever held a row: a member who died
    meanwhile or stepped off the hull is not this hull's to kill.

    **One hull at a time.** A transaction that loses several -- the helm
    striking two in one pass, a companion lost with its hull, the life support
    over a fleet -- takes their crews hull by hull, and across hulls the id
    order is not kept. Rows of two hulls' crews are held together only by
    another of the world's sweeps (a command holds one body, a handover two in
    one room), and a knot between two of the worker's jobs is replayed by its
    retry (`jobs._mark_failure`).
    """
    aboard = {node.id for node in await nodes_of(session, ship)}
    found = await _living_in(session, aboard)
    return [
        body
        for body in await world.lock_bodies(session, [body.id for body in found])
        if body.state is BodyState.ALIVE and body.node_id in aboard
    ]


async def _living_in(session: AsyncSession, nodes: set[uuid.UUID]) -> list[Body]:
    if not nodes:  # pragma: no cover -- a ship always has its connector
        return []
    return list(
        (
            await session.execute(
                select(Body)
                .where(Body.node_id.in_(nodes), Body.state == BodyState.ALIVE)
                #: In id order, the one every holder of several bodies keeps
                #: (`world.lock_bodies`): the life support writes the crew row
                #: by row in this order (`oxygen._breathing`), and in any other
                #: it would hold one member while waiting for another that a
                #: handover between the two already holds.
                .order_by(Body.id)
            )
        )
        .scalars()
        .all()
    )
