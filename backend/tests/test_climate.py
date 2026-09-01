# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The place's climate (D-261).

Checked is what the mechanic is built on:

* the day breathes: midnight is the mean minus the swing, noon plus it,
  and the phase counts from the world's epoch -- the clock the client draws;
* light is the place's sky: the woods take a step, buildings take a step,
  night takes everything;
* a node without a temperature record has no climate and no gate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.engine import climate, world
from src.models.estate import Building
from src.models.world import Planet


async def _place(session: AsyncSession, properties: dict):
    stamp = uuid.uuid4().hex[:8]
    return await world.create_node(
        session, f"terra.sky.{stamp}", "Место", area_m2=100, properties=properties
    )


async def test_the_day_breathes_between_the_swing_edges(
    session: AsyncSession, constants: Constants
) -> None:
    node = await _place(session, {"temperature": 20})
    origin = await world.epoch(session)
    assert origin is not None

    day = climate.day_hours_of(constants, Planet.TERRA)
    swing = climate.swing_of(constants, Planet.TERRA)
    midnight = origin
    noon = origin + timedelta(hours=day / 2)

    assert climate.temperature_now(constants, node, origin, midnight) == pytest.approx(20 - swing)
    assert climate.temperature_now(constants, node, origin, noon) == pytest.approx(20 + swing)
    assert not climate.is_day(constants, Planet.TERRA, origin, midnight)
    assert climate.is_day(constants, Planet.TERRA, origin, noon)


async def test_each_planet_counts_its_own_day(constants: Constants) -> None:
    """Terra's 38 hours are nobody else's (OQ-028): the clock and the phase
    must both pick the day by the planet."""
    lengths = {climate.day_hours_of(constants, planet) for planet in Planet}
    assert len(lengths) == len(list(Planet)), "у планет разные сутки"


async def test_light_is_the_places_sky(session: AsyncSession, constants: Constants) -> None:
    """The woods take a step and buildings take a step; night takes everything."""
    open_ground = await _place(session, {"temperature": 20})
    assert await climate.daylight(session, constants, open_ground) == climate.FULL_LIGHT

    grove = await _place(session, {"temperature": 20, "woods": True})
    assert await climate.daylight(session, constants, grove) == climate.FULL_LIGHT - 1

    #: Built over past the share: the yard loses another step of sky.
    session.add(Building(node_id=grove.id, area_m2=30, footprint_m2=30, floors=1))
    await session.flush()
    assert await climate.daylight(session, constants, grove) == climate.FULL_LIGHT - 2

    origin = await world.epoch(session)
    midnight = origin
    assert await climate.light_now(session, constants, grove, origin, midnight) == 0


async def test_a_node_without_a_record_has_no_climate(
    session: AsyncSession, constants: Constants
) -> None:
    bare = await _place(session, {"fertility": 40})
    assert climate.mean_temperature(bare) is None
    assert climate.temperature_now(constants, bare, datetime.now(UTC), datetime.now(UTC)) is None
    assert climate.precipitation(bare) == 0.0
