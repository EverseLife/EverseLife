# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Governing happens in the administration, and this is the check that says so.

A question of **presence**, not of authority: whether a living body is
standing in this city's administration, warm and not cut off (D-155, D-231).
Who may do the thing is `office`'s question and a different one entirely.

It lives apart because of what it drags in. Answering it needs `travel`,
`world`, `utility` and -- lazily, to break a cycle -- `frost`, and while it
sat among the offices every module that wanted `require` ("has this person
the right?") imported all of that with it. `office` is the bottom of the
city's stack and asks nobody anything; this asks half the engine, so the two
do not belong in one file.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import current
from src.engine import travel, utility, world
from src.engine.city._base import (
    HALL,
    NotAllowed,
)
from src.models.city import (
    City,
)
from src.models.identity import BodyState
from src.models.inventory import Item
from src.models.world import Node


async def require_at_hall(session: AsyncSession, body, city: City) -> None:
    """Governing is done **in the administration** of this city (D-155).

    Authority that can be exercised from across the ocean needs neither a
    capital nor roads to it: the administration becomes decoration, and seizing
    power a matter of one click rather than geography.

    Reading the panel is unaffected: figures travel over the Net (D-140).
    """

    if body is None or body.state is not BodyState.ALIVE:
        raise NotAllowed(key="city-hall-dead")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise NotAllowed(key="city-body-off-node")
    if node.owner_city_id != city.id:
        raise NotAllowed(key="city-hall-not-territory", city=city.name)
    yard = await world.node_container(session, node)
    costs = await session.scalar(
        select(Item.id)
        .where(
            Item.container_id == yard.id,
            Item.type_key.in_(world.station_names(HALL)),
        )
        .limit(1)
    )
    if costs is None:
        raise NotAllowed(key="city-hall-absent")
    if await utility.cut_off(session, node):
        raise NotAllowed(key="city-hall-cut-off")
    #: A frozen node closes the administration as surely as an unpaid bill
    #: does (D-231): heat is a condition of the office, not its comfort.
    from src.engine import frost  # noqa: PLC0415 -- lazy: breaks the import cycle with frost

    if not await frost.is_warm(session, current(), node):
        raise NotAllowed(key="city-hall-frozen", node=node.name)
