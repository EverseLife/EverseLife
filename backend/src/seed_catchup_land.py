# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The catch-up's repairs of city land (D-282, D-356).

Cut out of `seed_catchup.py` when it passed the eight hundred lines the
quality bar allows (2026-09-19): the city's own locations taken back from a
private title, and the land a highway took before D-356 made a plot of it.
The catch-up calls both in the order it always did.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import city as town
from src.engine import ruins
from src.models.city import City
from src.models.world import ABOARD, PLOT, Layer, Node

log = logging.getLogger("everselife.seed")


async def return_city_locations(session: AsyncSession) -> None:
    """Give the city back its own locations (D-282).

    Only what is not a plot: an allotted or bought plot is its holder's, door
    and all, and nothing here touches it. What comes back is the core, the
    market, the administration -- the places a city works from, which were
    never anybody's to hold.
    """
    taken = await town.reclaim_all(session)
    for city, node in taken:
        log.info("city location returned to %s: %s", city.name, node.key)
    if taken:
        await session.flush()


async def taken_land_is_plots(session: AsyncSession) -> list[str]:
    """Mark as plots the finds a highway took before D-356.

    A city's own locations are its node and what hangs on it (D-282); a node
    the city holds that hangs anywhere else -- on the planet, under a ruin --
    came to it by a highway (D-332), and since D-356 such land is sold and
    handed out like a ring's. Returns the keys it marked.
    """
    #: The cities that stand on the ground: a world the old catch-up hurt has
    #: one founded on its planet's sphere (D-356 item 10), and the finds it
    #: wrote to itself are no highway's.
    grounded = (
        select(City.id, City.node_id)
        .join(Node, Node.id == City.node_id)
        .where(Node.layer == Layer.PLANET)
        .subquery()
    )
    homes = select(grounded.c.node_id)
    #: A Forerunner ruin a highway took -- the root of a lost city and all
    #: that hangs on it -- is the city's land and not a plot (D-356): the
    #: root known by `precursors` and the ruin's own `city` mark together,
    #: as `city.line.of_the_forerunners` knows it.
    roots = select(Node.id).where(
        Node.layer == Layer.PLANET,
        Node.properties.has_key(ruins.PRECURSOR),
        Node.properties.has_key(ruins.KIND),
    )
    nodes = (
        (
            await session.execute(
                select(Node).where(
                    Node.owner_city_id.in_(select(grounded.c.id)),
                    Node.layer == Layer.PLANET,
                    Node.id.not_in(homes),
                    Node.parent_id.not_in(homes),
                    Node.id.not_in(roots),
                    Node.parent_id.not_in(roots),
                    ~Node.properties.has_key(ABOARD),
                    ~Node.properties.has_key(PLOT),
                )
            )
        )
        .scalars()
        .all()
    )
    for node in nodes:
        node.properties = {**(node.properties or {}), PLOT: True}
        log.info("land a highway took is a plot now: %s", node.key)
    if nodes:
        await session.flush()
    return [node.key for node in nodes]
