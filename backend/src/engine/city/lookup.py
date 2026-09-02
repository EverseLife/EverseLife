# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""city: lookup.

Split out of `engine/city.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import travel, world
from src.engine.death import PRECURSOR
from src.models.city import (
    City,
)
from src.models.world import Layer, Node, storey_of


async def by_id(session: AsyncSession, city_id: uuid.UUID) -> City | None:
    return await session.get(City, city_id)


async def by_node(session: AsyncSession, node_id: uuid.UUID) -> City | None:
    """The city whose delegate node this is."""
    return (await session.execute(select(City).where(City.node_id == node_id))).scalar_one_or_none()


async def by_name(session: AsyncSession, name: str) -> City | None:
    """The city of that name, case ignored.

    Case is ignored because the name becomes the name of the city's official
    channel, and the Net compares channel names that way (`net.create_channel`):
    a rule that told "Novograd" from "novograd" here would still hand the Net
    two channels it calls the same.

    Both sides are folded by the database and neither by Python: the index that
    holds the rule is `lower(name)` in Postgres, and Python's `str.lower` parts
    from it on code points like "I" with a dot. A question answered one way and
    enforced another would miss exactly the names it was asked about.
    """
    return (
        await session.execute(select(City).where(func.lower(City.name) == func.lower(name)))
    ).scalar_one_or_none()


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
    if parent is None:
        return None
    #: A storey stands in whatever city the plot under it stands in (D-247): a
    #: workshop on the third floor is inside the walls exactly as much as the
    #: yard below it, and laws, taxes, the market and the boundary check on the
    #: stairs must not think otherwise. A compartment aboard is not a storey and
    #: still belongs to no city (D-202) -- the answer it had before.
    if storey_of(node) is not None:
        return await of_node(session, parent)
    if parent.layer is not Layer.PLANET:
        return None
    return await by_node(session, parent.id)


async def territory(session: AsyncSession, city: City) -> Sequence[Node]:
    """Every node of the city: the delegate, its built-up area, its land.

    The same three ways of belonging `of_node` reads, only from the other end
    -- with one exception, and it is deliberate: **the floors of a house are
    not listed** (D-247). `of_node` climbs from a storey to the plot under it,
    because laws and taxes must not stop at the first floor; this list is what
    the city walks over to find its printers, its gate and its meters, and a
    floor has none of those. Adding them would make every such walk longer by
    the height of the city and answer nothing new.
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


async def core(session: AsyncSession, city: City) -> Node | None:
    """The city core -- the node with the bioprinter the city grew from (D-089).

    A city is founded where a bioprinter already stands (`establish`), so a city
    on one node is its own core: that very machine became the ground of the
    city. The capital is laid out otherwise -- its delegate node holds no
    machines -- and there the core is the node under it the capital was rebuilt
    from, the one with the Forerunners' Printer.

    Only the core is a door into the world (D-208, `world.is_door`). Printers
    built later print the dead and the returning, but a newcomer does not come
    out of somebody's workshop -- so which node this is has to be answered the
    same way every time, and the capital has three printers to choose between.

    **Asked of the world, not of a mark on it.** There used to be a «кольцо»
    property here: nought meant the centre, and the layout wrote it by hand.
    It was a second opinion about a number the engine already measures for
    itself -- `estate.price.nodes_from_center` walks the edges, because edges
    are how people actually cross a city and a property written at generation
    is not. Two opinions about one number is one of them being wrong later.

    So the core is recognised by two facts of the world instead:

    * **the Forerunners' machine**, where there is one. The capital's printer
      is a relic and there will never be a second (D-028), so nothing else can
      be mistaken for the city it stands in;
    * **age** otherwise. "The node the city grew from" is meant literally: of
      the printers a city holds, the oldest is the one it grew from, and the
      forge got its own later by somebody's work.
    """

    own = await session.get(Node, city.node_id)
    if own is not None and await world.has_station(session, own, world.BIOPRINTER):
        return own

    printers = [
        place
        for place in sorted(await territory(session, city), key=lambda one: one.created_at)
        if await world.has_station(session, place, world.BIOPRINTER)
    ]
    for place in printers:
        if (place.properties or {}).get(PRECURSOR):
            return place
    return printers[0] if printers else None


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
