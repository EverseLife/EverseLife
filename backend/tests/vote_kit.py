# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The vote tests' shared ground: a city that votes, a resident in it, a law
put to the poll and the tally run the way the worker runs it.

Used by the `test_vote*.py` family; not collected by pytest. No real fixture
lives here on purpose -- a `@pytest.fixture` in a kit is imported for its name
alone, ruff removes the import as unused, and pytest then cannot find it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog
from src.engine import city as town
from src.engine import vote, world
from src.models.city import Citizen
from src.models.job import Job, JobKind, JobState
from src.models.vote import Vote
from src.models.world import Layer

LAW, VALUE = "tax_trade", "7"


async def _city(session: AsyncSession, catalog: Catalog, **charter):
    """A city that gave laws to citizens, and its ruler."""
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session,
        f"terra.city.{stamp}",
        "Вече",
        area_m2=1,
        layer=Layer.PLANET,
        parent=planet,
    )
    core = await world.create_node(
        session,
        f"terra.city.{stamp}.core",
        "Ядро",
        area_m2=100,
        parent=delegate,
        properties={"ring": 0},
    )
    city = await town.found(session, catalog, delegate, "Вече")
    core.owner_city_id = city.id
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, town.HALL, quality=65, origin="тест")
    city.charter = {**city.charter, vote.APPROVAL: vote.BY_CITIZENS, **charter}
    await session.flush()

    ruler, body = await _resident(session, core, city, "Правитель")
    await town.install_founder(session, city, ruler)
    return city, core, ruler, body


async def _resident(session: AsyncSession, node, city, name: str, *, citizen=True):
    identity = await world.create_identity(session, f"{name}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    if citizen:
        session.add(Citizen(identity_id=identity.id, city_id=city.id))
        await session.flush()
    return identity, body


async def _convene(session, constants, catalog, city, ruler, body) -> Vote:
    await town.set_law(session, constants, catalog, ruler, city, LAW, VALUE, body=body)
    going = await vote.open_votes(session, city)
    assert len(going) == 1
    return going[0]


async def _bring(session: AsyncSession, poll: Vote) -> None:
    """Run the tally -- the same way the worker would."""

    job = (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.VOTE_CLOSE.value, Job.state == JobState.PENDING
                )
            )
        )
        .scalars()
        .first()
    )
    assert job is not None
    await vote.close(session, job)
    job.state = JobState.DONE
    await session.flush()
