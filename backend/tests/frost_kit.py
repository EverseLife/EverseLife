# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The cold's shared fixtures: a climate sphere, a town under it, a dweller,
a thing laid in the yard, a charged pool, an hour ago. Used by both frost
files (`test_frost*.py`); not collected by pytest.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.engine import energy, frost, world
from src.models.identity import Body
from src.models.world import Layer, Node, Planet

HEATER = "heater"


async def _sphere(session: AsyncSession, planet: Planet, climate: str | None) -> Node:
    """The planet's node on the space layer -- the seed lays exactly this (D-231)."""
    return await world.create_node(
        session,
        planet.value,
        planet.value,
        planet=planet,
        area_m2=1,
        layer=Layer.SPACE,
        properties={} if climate is None else {climate: True},
    )


async def _town(
    session: AsyncSession, *, planet: Planet = Planet.AURORA, climate: str | None = frost.FROST
) -> tuple[Node, Node]:
    """A city on the planet: a delegate node with one built-up node under it."""
    sphere = await _sphere(session, planet, climate)
    stamp = uuid.uuid4().hex[:8]
    city = await world.create_node(
        session,
        f"{planet.value}.city.{stamp}",
        "Город",
        planet=planet,
        area_m2=1,
        layer=Layer.PLANET,
        parent=sphere,
    )
    yard = await world.create_node(
        session,
        f"{planet.value}.city.{stamp}.yard",
        "Двор",
        planet=planet,
        area_m2=200,
        layer=Layer.CITY,
        parent=city,
    )
    return city, yard


async def _dweller(session: AsyncSession, node: Node) -> Body:
    identity = await world.create_identity(session, f"Житель-{uuid.uuid4().hex[:6]}")
    return await world.print_body(session, identity, node)


async def _place(session: AsyncSession, node: Node, what: str, qty: float = 1) -> None:
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, what, amount=qty, quality=60, origin="тест")


async def _charge(session: AsyncSession, constants: Constants, node: Node, stored: float) -> None:
    """Put energy into the city pool by hand: generation is another test's business."""
    pool = await energy.pool_of(session, constants, node)
    assert pool is not None
    pool.stored = Decimal(str(stored))
    pool.counted_at = datetime.now(UTC)
    await session.flush()


def _ago(hours: float) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)
