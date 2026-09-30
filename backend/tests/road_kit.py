# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the road tests share: an edge with a crew at one end, and the job run
to its end. Not collected by pytest -- helpers only, no fixtures."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import road, travel, world
from src.models.world import Surface


async def _edge(
    session: AsyncSession, *, surface: Surface = Surface.TRAIL, surface_amount: float = 0
):
    stamp = uuid.uuid4().hex[:8]
    here = await world.create_node(session, f"terra.rda.{stamp}", "Здесь", area_m2=100)
    there = await world.create_node(session, f"terra.rdb.{stamp}", "Там", area_m2=100)
    edge = await travel.connect(session, here, there, base_seconds=600, surface=surface)
    identity = await world.create_identity(session, f"Дорожник-{stamp}")
    body = await world.print_body(session, identity, here)
    if surface_amount:
        pocket = await world.body_container(session, body)
        await world.grant_item(
            session,
            pocket,
            "road_paving",
            amount=surface_amount,
            origin="сценарий теста",
        )
    return here, there, body, edge


async def _finish(session: AsyncSession, job) -> None:
    """Run the work to the end -- the same way the worker would."""
    from src.models.job import JobState

    await road.finished(session, job)
    job.state = JobState.DONE
    await session.flush()
