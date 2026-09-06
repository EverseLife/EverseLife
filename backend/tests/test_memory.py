# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Memory instead of fog (D-319 п. 6): the places one has been to.

An arrival writes the place into the identity's knowledge; a second visit
renews the moment; beyond the ceiling the oldest is forgotten without pins;
death takes nothing (I8). Written by the job, never by a read.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Constants
from src.engine import jobs, memory, travel, world
from src.models.identity import Body, Knowledge, KnowledgeKind
from src.models.world import Layer, Planet, Surface


async def _identity(session: AsyncSession) -> uuid.UUID:
    return (await world.create_identity(session, f"Walker-{uuid.uuid4().hex[:6]}")).id


async def test_a_place_is_remembered_once_and_renewed(
    session: AsyncSession, constants: Constants
) -> None:
    who = await _identity(session)
    first = datetime.now(UTC)
    await memory.remember(session, constants, who, ["terra.a", "terra.b", "terra.a"], at=first)
    assert await memory.known(session, who) == {"terra.a", "terra.b"}
    later = first + timedelta(hours=1)
    await memory.remember(session, constants, who, ["terra.a"], at=later)
    rows = (
        (
            await session.execute(
                select(Knowledge).where(
                    Knowledge.identity_id == who, Knowledge.kind == KnowledgeKind.PLACE
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2, "повторный визит — та же строка"
    assert {row.key: row.acquired_at for row in rows}["terra.a"] == later


async def test_beyond_the_ceiling_the_oldest_is_forgotten(
    session: AsyncSession, constants: Constants
) -> None:
    """Recency and nothing else: the first place visited goes first, whatever it was."""
    who = await _identity(session)
    start = datetime.now(UTC)
    for number in range(5):
        await memory.remember(
            session,
            constants,
            who,
            [f"terra.{number}"],
            at=start + timedelta(minutes=number),
            cap=3,
        )
    assert await memory.known(session, who) == {"terra.2", "terra.3", "terra.4"}
    #: A revisit of the oldest survivor makes it the newest: something else goes.
    await memory.remember(
        session, constants, who, ["terra.2"], at=start + timedelta(minutes=9), cap=3
    )
    await memory.remember(
        session, constants, who, ["terra.5"], at=start + timedelta(minutes=10), cap=3
    )
    assert await memory.known(session, who) == {"terra.2", "terra.4", "terra.5"}


async def test_an_arrival_writes_the_place_and_death_keeps_it(
    factory: async_sessionmaker[AsyncSession], constants: Constants
) -> None:
    async with factory() as session, session.begin():
        terra = await world.create_node(
            session, "terra", "Terra", area_m2=1, planet=Planet.TERRA, layer=Layer.SPACE
        )
        here = await world.create_node(session, "terra.here", "Here", area_m2=100, parent=terra)
        there = await world.create_node(session, "terra.there", "There", area_m2=100, parent=terra)
        await travel.connect(session, here, there, base_seconds=30, surface=Surface.ROAD)
        identity = await world.create_identity(session, "Walker")
        body = await world.print_body(session, identity, here)
        body.stamina = 50
        await session.flush()
        leg = await travel.depart(session, constants, body, there)
        term, who, body_id = leg.arrives_at, identity.id, body.id
    assert await jobs.run_one(factory, now=term) is not None
    async with factory() as session, session.begin():
        assert "terra.there" in await memory.known(session, who), "прибытие записало место"
        body = await session.get(Body, body_id)
        assert body is not None
        from src.models.identity import BodyState

        body.state = BodyState.DEAD
    async with factory() as session:
        assert "terra.there" in await memory.known(session, who), "смерть память не отнимает (I8)"
