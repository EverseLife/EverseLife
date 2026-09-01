# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The black planet's shared fixtures: the sphere, a stretch of surface, a
dweller on it. Used by the pyroxis files (`test_pyroxis*.py`); not collected
by pytest.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import plates, ship, travel, world
from src.models.identity import Body
from src.models.world import Layer, Node, Planet, Surface


async def _pyroxis(session: AsyncSession) -> Node:
    return await world.create_node(
        session,
        "pyroxis",
        "Пироксис",
        planet=Planet.PYROXIS,
        area_m2=1,
        layer=Layer.SPACE,
        properties={ship.OPEN_LANDING: True, "heat": True},
    )


async def _surface(
    session: AsyncSession, count: int = 3, *, chain: bool = True
) -> tuple[Node, list[Node]]:
    """The plateau and a few fields around it, connected like the seed's.

    `chain=False` leaves the seed's own shape: a star, every field hanging on
    the plateau alone. That is the state a fresh world is in, and the state in
    which no way out of a field may go at all.
    """
    sphere = await _pyroxis(session)
    stamp = uuid.uuid4().hex[:6]
    plateau = await world.create_node(
        session,
        f"pyroxis.{stamp}.anvil",
        "Плато",
        planet=Planet.PYROXIS,
        area_m2=1,
        layer=Layer.PLANET,
        parent=sphere,
        properties={plates.ANVIL: True},
    )
    fields = []
    for number in range(count):
        field = await world.create_node(
            session,
            f"pyroxis.{stamp}.field.{number}",
            f"Поле {number}",
            planet=Planet.PYROXIS,
            area_m2=5000,
            layer=Layer.PLANET,
            parent=sphere,
        )
        await travel.connect(session, plateau, field, base_seconds=900, surface=Surface.TRAIL)
        fields.append(field)
    #: The fields are neighbours of each other too, or an eruption would have
    #: nowhere to move a vein to.
    if chain:
        for one, other in zip(fields, fields[1:], strict=False):
            await travel.connect(session, one, other, base_seconds=900, surface=Surface.TRAIL)
    return plateau, fields


async def _dweller(session: AsyncSession, node: Node) -> Body:
    identity = await world.create_identity(session, f"Вахтовик-{uuid.uuid4().hex[:6]}")
    return await world.print_body(session, identity, node)
