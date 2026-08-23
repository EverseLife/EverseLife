# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""city: lookup.

Split out of `engine/city.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import travel, world
from src.engine.death import PRECURSOR
from src.models.city import (
    City,
)
from src.models.world import Layer, Node


async def by_id(session: AsyncSession, city_id: uuid.UUID) -> City | None:
    return await session.get(City, city_id)


async def by_node(session: AsyncSession, node_id: uuid.UUID) -> City | None:
    """The city whose delegate node this is."""
    return (await session.execute(select(City).where(City.node_id == node_id))).scalar_one_or_none()


async def of_node(session: AsyncSession, node: Node) -> City | None:
    """The city on whose territory the node stands.

    A city's territory is its children in the display hierarchy (D-045). The
    floodplain and the mine hang directly on the planet and are covered by no
    authority -- there are no laws there, and that is geography, not an omission.
    """
    if node.owner_city_id is not None:
        return await by_id(session, node.owner_city_id)
    #: The delegate node is the territory of its own city (D-159). Otherwise a
    #: person standing in it is formally outside the city, and in-person
    #: authority in a just-founded city turns out to be unreachable.
    own = await by_node(session, node.id)
    if own is not None:
        return own
    if node.parent_id is None:
        return None
    parent = await session.get(Node, node.parent_id)
    if parent is None or parent.layer is not Layer.PLANET:
        return None
    return await by_node(session, parent.id)


async def territory(session: AsyncSession, city: City) -> Sequence[Node]:
    """Every node of the city: the delegate, its built-up area, its land.

    The same three ways of belonging `of_node` reads, only from the other end.
    """
    return (
        (
            await session.execute(
                select(Node).where(
                    (Node.owner_city_id == city.id)
                    | (Node.id == city.node_id)
                    | (Node.parent_id == city.node_id)
                )
            )
        )
        .scalars()
        .all()
    )


#: Node property: the ring of the built-up area, a record made at generation
#: (D-089). The zero ring is the centre, and the bioprinter stands in it.
RING = "кольцо"


async def core(session: AsyncSession, city: City) -> Node | None:
    """The city core -- the node with the bioprinter the city grew from (D-089).

    A city is founded where a bioprinter already stands (`establish`), so a city
    on one node is its own core: that very machine became the ground of the
    city. The capital is laid out otherwise -- the delegate node holds no
    machines -- and there the core is the zero ring under it, the node with the
    Forerunners' Printer the capital was rebuilt from.

    Only the core is a door into the world (D-208, `world.is_door`). Printers
    built later print the dead and the returning, but a newcomer does not come
    out of somebody's workshop.
    """

    own = await session.get(Node, city.node_id)
    if own is not None and await world.has_station(session, own, world.BIOPRINTER):
        return own
    #: The centre of the built-up area is marked twice -- by the zero ring and by
    #: the Forerunners' machine -- and either mark will do: a world laid out
    #: before one of them still has a core rather than none.
    for place in await territory(session, city):
        marks = place.properties or {}
        if not (marks.get(PRECURSOR) or marks.get(RING) == 0):
            continue
        if await world.has_station(session, place, world.BIOPRINTER):
            return place
    return None


async def gate(session: AsyncSession, city: City) -> Node | None:
    """The city's gate: where the built-up area meets the road beyond it (D-206).

    Founding marks one, so a live city always has it. Nothing comes back only
    for a city from before that decision which the catch-up seed has not reached
    yet -- and then a road into it is refused rather than tied to a random node.
    """

    for node in await territory(session, city):
        if (node.properties or {}).get(travel.EXIT):
            return node
    return None
