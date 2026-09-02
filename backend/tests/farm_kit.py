# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the farm tests share: the farmstead and the crops they sow.

A kit, not a conftest: pytest does not collect it, and a real fixture must
not live here (CLAUDE.md) -- these are plain helpers imported by name.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import world

SPELT = "spelt"
BEANS = "beans"
#: The least demanding crop of the catalog: its fertility norm is 10, which is
#: exactly what made the uncapped soil share a tenfold multiplier (OQ-107).
BROME = "brome"


async def _farmstead(
    session: AsyncSession, *, water: str = "river", fertility: float = 55, area: float = 200
):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session,
        f"terra.farm.{stamp}",
        "Хутор",
        area_m2=area,
        properties={"water": water, "fertility": fertility},
    )
    identity = await world.create_identity(session, f"Фермер-{stamp}")
    body = await world.print_body(session, identity, node)
    #: The holder runs the estate: the fixture's farmer has already taken their plot.
    node.owner_identity_id = identity.id
    await session.flush()
    return node, identity, body
