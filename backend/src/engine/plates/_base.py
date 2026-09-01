# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The ground the eruption machinery stands on: what is exempt, what is
surface, and the planet's graph read whole.

Everything here only reads. The floor of the package: every other room asks
these questions, and this one asks nobody back.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import ship
from src.models.ship import Ship
from src.models.world import Edge, Layer, Node, Planet

#: The node property that keeps a place out of every draw (D-197): the Anvil
#: Plateau, and whatever else a planet's seed marks the same way.
ANVIL = "anvil"


async def _exempt(session: AsyncSession) -> set[uuid.UUID]:
    """The ground no eruption touches: the plateau and whatever a ship stands on.

    Asked twice -- when the nodes are chosen and again when they are shaken --
    because six hours pass between the two, and a ship may land inside them.
    """
    anvils = (
        (
            await session.execute(
                select(Node.id).where(
                    Node.planet == Planet.PYROXIS.value,
                    Node.properties[ANVIL].as_boolean(),
                )
            )
        )
        .scalars()
        .all()
    )
    moored = (
        (await session.execute(select(Ship.docked_node_id).where(Ship.docked_node_id.is_not(None))))
        .scalars()
        .all()
    )
    return {one for one in [*anvils, *moored] if one is not None}


async def _surface(session: AsyncSession) -> list[Node]:
    """Every node of the planet's ground. Not the sphere, not a ship's rooms."""
    found = (
        (
            await session.execute(
                select(Node).where(Node.planet == Planet.PYROXIS.value, Node.layer != Layer.SPACE)
            )
        )
        .scalars()
        .all()
    )
    return [node for node in found if not ship.is_aboard(node)]


async def _adjacency(session: AsyncSession) -> dict[uuid.UUID, set[uuid.UUID]]:
    """The planet's whole graph in one reading: node -> its neighbours.

    One query, because everything that uses this walks the graph -- what may
    break, what is still reachable, where a vein may move -- and asking the
    database per edge would grow with the planet.
    """
    ground = {node.id for node in await _surface(session)}
    edges = (
        (
            await session.execute(
                select(Edge).where(or_(Edge.node_a_id.in_(ground), Edge.node_b_id.in_(ground)))
            )
        )
        .scalars()
        .all()
    )
    ways: dict[uuid.UUID, set[uuid.UUID]] = {}
    for edge in edges:
        ways.setdefault(edge.node_a_id, set()).add(edge.node_b_id)
        ways.setdefault(edge.node_b_id, set()).add(edge.node_a_id)
    return ways


def _connected(ways: dict[uuid.UUID, set[uuid.UUID]], start: uuid.UUID) -> set[uuid.UUID]:
    """Everything reachable from here by the ways given."""
    seen = {start}
    queue = [start]
    while queue:
        where = queue.pop()
        for other in ways.get(where, set()):
            if other not in seen:
                seen.add(other)
                queue.append(other)
    return seen
