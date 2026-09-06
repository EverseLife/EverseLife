# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The public map is a snapshot with a delay (D-319 п. 7).

The daily tick writes the public surface as rows; the route serves the
newest snapshot at least `map.public_delay_days` old and nothing younger;
the tick prunes what is older than the one served. The rows are the map's
own rows, so a snapshot and a live map cannot drift apart.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import mapshot, travel, world
from src.models.snapshot import MapSnapshot
from src.models.world import ABOARD, Layer, Node, Planet, Surface


async def _world(session: AsyncSession) -> tuple[Node, Node, Node]:
    terra = await world.create_node(
        session, "terra", "Terra", area_m2=1, planet=Planet.TERRA, layer=Layer.SPACE
    )
    city = await world.create_node(session, "terra.city", "City", area_m2=1, parent=terra)
    plot = await world.create_node(session, "terra.city.plot", "Plot", area_m2=100, parent=city)
    await travel.connect(session, city, plot, base_seconds=20, surface=Surface.PAVED)
    hull = await world.create_node(
        session, "ship.x", "Hull", area_m2=1, planet=Planet.TERRA, layer=Layer.SPACE
    )
    await world.create_node(
        session,
        "ship.x.room",
        "Room",
        area_m2=40,
        layer=Layer.LOCATION,
        parent=hull,
        properties={ABOARD: True},
    )
    return terra, city, plot


async def test_the_snapshot_carries_the_surface_and_not_the_insides(
    session: AsyncSession, constants: Constants
) -> None:
    terra, city, plot = await _world(session)
    taken = await mapshot.take(session, constants, datetime.now(UTC))
    keys = {row["key"] for row in taken.data["nodes"]}
    assert {"terra.city", "terra.city.plot"} <= keys
    assert "terra" not in keys, "небо в снимке не лежит: оно арифметика и всегда живое"
    assert "ship.x.room" not in keys, "борт не публичен (D-201)"
    plot_row = next(row for row in taken.data["nodes"] if row["key"] == "terra.city.plot")
    assert plot_row["parent"] == "terra.city" and "faded" not in plot_row
    way = taken.data["edges"]
    assert len(way) == 1 and (way[0]["a"], way[0]["b"], way[0]["surface"]) == (
        "terra.city",
        "terra.city.plot",
        "paved",
    )
    assert taken.data["edges"][0]["seconds"] > 0


async def test_the_route_serves_only_a_snapshot_old_enough_and_the_tick_prunes(
    session: AsyncSession, constants: Constants
) -> None:
    await _world(session)
    delay = timedelta(days=float(constants[R.MAP_PUBLIC_DELAY_DAYS]))
    now = datetime.now(UTC)
    old = await mapshot.take(session, constants, now - delay * 3)
    older = await mapshot.take(session, constants, now - delay * 2)
    fresh = await mapshot.take(session, constants, now - delay / 2)
    served = await mapshot.served(session, constants, now)
    assert served is not None and served.id == older.id, "самый свежий из достаточно старых"
    assert served.id != fresh.id, "свежее задержки не отдаётся"
    assert await mapshot.served(session, constants, now - delay * 4) is None
    #: The tick prunes what is older than the one served, and keeps the rest.
    assert await mapshot.prune(session, constants, now) == 1
    left = set((await session.execute(select(MapSnapshot.id))).scalars())
    assert left == {older.id, fresh.id} and old.id not in left
    assert await session.scalar(select(func.count()).select_from(MapSnapshot)) == 2
