# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Exploration's shared fixtures: a scout on the frontier, a townsman inside
walls, a place walked over, a run brought home. Used by both explore files
(`test_explore*.py`); not collected by pytest.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import explore, world
from src.models.job import Job, JobKind, JobState
from src.models.world import Layer


async def _scout(session: AsyncSession):
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    gate = await world.create_node(
        session,
        f"terra.gate.{stamp}",
        "Выход",
        area_m2=80,
        layer=Layer.PLANET,
        parent=planet,
    )
    identity = await world.create_identity(session, f"Разведчик-{stamp}")
    body = await world.print_body(session, identity, gate)
    return planet, gate, body


async def _return(session: AsyncSession, body) -> None:
    """Run the run to the end -- the same way the worker would."""
    job = (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.EXPLORE_SURVEY.value,
                    Job.body_id == body.id,
                    Job.state == JobState.PENDING,
                )
            )
        )
        .scalars()
        .first()
    )
    assert job is not None
    await explore.returned(session, job)
    job.state = JobState.DONE
    await session.flush()
