# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A second floor laid on its own ground floor is seated off it at the next deploy, once.

The addendum to D-247 (2026-09-19): the plot stands at the origin of its
floors' plan, and a floor opened before that rule may stand there too. The
catch-up moves it once per world (`seed_once.FLOORS_OFF_THE_GROUND`); a world
laid today is born past the step.
"""

from __future__ import annotations

import math
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import seed_once
from src.engine import places, props, world
from src.models.catchup import CatchUpStep
from src.models.world import STOREY, Layer, Node
from src.runtime import MAP_MIN_GAP
from src.seed import seed

STEP = seed_once.FLOORS_OFF_THE_GROUND


async def _marks(session: AsyncSession) -> list[CatchUpStep]:
    return list(
        (await session.execute(select(CatchUpStep).where(CatchUpStep.step == STEP))).scalars()
    )


async def _floor_on_the_ground(session: AsyncSession, core: Node) -> Node:
    """A house of two storeys as the old rule laid it: the second floor at the origin."""
    plot = await world.create_node(
        session, f"terra.lot.{uuid.uuid4().hex[:8]}", "Участок", area_m2=200,
        layer=Layer.PLANET, parent=await session.get(Node, core.parent_id), anchor=core,
    )  # fmt: skip
    floor = await world.create_node(
        session, f"{plot.key}.floor.2", "2-й этаж", area_m2=40,
        layer=Layer.LOCATION, parent=plot, anchor=plot,
    )  # fmt: skip
    await props.stamp(
        session, floor, {STOREY: 2, places.PLACE: {places.PLACE_X: 0.0, places.PLACE_Y: 0.0}}
    )
    return floor


async def test_the_next_deploy_seats_the_floor_off_its_ground_floor_once(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(seed_once, "ONCE", tuple(step for step in seed_once.ONCE if step != STEP))
        core = await seed(session)
    assert await _marks(session) == []
    floor = await _floor_on_the_ground(session, core)

    await seed(session)
    moved = places.place_of(floor)
    assert moved is not None and math.hypot(*moved) >= MAP_MIN_GAP
    [mark] = await _marks(session)
    assert mark.result == {"floors": 1}

    #: Once: the next deploy asks nothing and moves nothing.
    await seed(session)
    assert places.place_of(floor) == moved
    assert len(await _marks(session)) == 1


async def test_a_world_laid_fresh_owes_no_run(session: AsyncSession) -> None:
    await seed(session)
    [mark] = await _marks(session)
    assert mark.result == {"born": True}
