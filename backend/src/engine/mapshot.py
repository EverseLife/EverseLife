# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The rows of the map, and the daily snapshot of the public ones (D-319 п. 7).

One shape for a node on the wire, whoever asks: the personal map of
`/public/map` with a token, and the delayed public map without one. The rows
are built here so the two cannot drift apart, and the snapshot stores them
as they are -- the route serves a snapshot without rebuilding a thing.

The snapshot is what the anonymous reader gets: the public part of every
planet's surface as it was `map.public_delay_days` ago. Every node is in it
-- nodes do not hide (D-319) -- and everything about them is old: which
ways are paved, which node is a port, what stands where. The daily tick
writes one and prunes the ones older than the one being served.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import estate, places, sight, travel, world
from src.engine import ship as vessels
from src.models.snapshot import MapSnapshot
from src.models.world import Edge, Layer, Node


def node_row(
    node: Node,
    *,
    parent_key: str | None,
    port: bool,
    flight: dict[str, Any] | None = None,
    faded: bool = False,
) -> dict[str, Any]:
    """A node as the map draws it (D-045, D-097, D-237, D-238)."""
    row: dict[str, Any] = {
        "key": node.key,
        "name": node.name,
        #: Layers are a display abstraction: the world stays one graph, and
        #: the parent hierarchy groups nodes by layer.
        "layer": node.layer.value,
        "parent": parent_key,
        "port": port,
        #: The space layer paints by planet and lays nodes out by orbit.
        "planet": node.planet.value,
        #: Where the node stands, once and for everybody (D-237). Empty on the
        #: space layer -- there a place is a function of time.
        "place": places.wire(node),
        "orbit": world.orbit_of(node),
        "deferred": bool((node.properties or {}).get(world.DEFERRED)),
        #: A ship is a group of ordinary nodes (D-201), and only this mark
        #: tells them from ground: one boards a hull by the gangway.
        "aboard": vessels.is_aboard(node),
        "flight": flight,
        #: Place signs: the map draws the node's glyph by them (D-238). An
        #: allowlist on purpose -- this answers the whole internet.
        "features": world.public_signs(node),
        #: The owner's mark, if one is nailed on (D-238).
        "emblem": estate.public_emblem(node),
    }
    #: Memory and the public are drawn dark (D-319 п. 6); sent only when so,
    #: so the bright majority of rows carry nothing for it.
    if faded:
        row["faded"] = True
    return row


def edge_row(constants: Constants, edge: Edge, by_key: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "a": by_key[edge.node_a_id],
        "b": by_key[edge.node_b_id],
        "surface": edge.surface.value,
        "seconds": round(travel.edge_seconds(constants, edge)),
    }


def _public_surface(nodes: list[Node]) -> list[Node]:
    """What the snapshot carries: the surfaces, without the insides.

    The rooms of a hull are not public (D-201), and the floors and rooms of
    the land are the inside window's, not the map's (D-319 п. 9).
    """
    return [node for node in nodes if node.layer is Layer.PLANET and not vessels.is_aboard(node)]


async def take(session: AsyncSession, constants: Constants, now: datetime) -> MapSnapshot:
    """Write the public map as it is now; the route will serve it when it is old enough."""
    every, all_edges = await sight.read(session)
    nodes = _public_surface(every)
    shown = {node.id for node in nodes}
    by_key = {node.id: node.key for node in nodes}
    ports = {node.id for node in await vessels.ports(session)}
    rows = [
        node_row(node, parent_key=by_key.get(node.parent_id), port=node.id in ports)
        for node in nodes
    ]
    edges = [
        edge_row(constants, edge, by_key)
        for edge in all_edges
        if edge.node_a_id in shown and edge.node_b_id in shown
    ]
    snapshot = MapSnapshot(taken_at=now, data={"nodes": rows, "edges": edges})
    session.add(snapshot)
    await session.flush()
    return snapshot


def _served_before(constants: Constants, now: datetime) -> datetime:
    return now - timedelta(days=float(constants[R.MAP_PUBLIC_DELAY_DAYS]))


async def served(session: AsyncSession, constants: Constants, now: datetime) -> MapSnapshot | None:
    """The newest snapshot that is at least `map.public_delay_days` old."""
    return await session.scalar(
        select(MapSnapshot)
        .where(MapSnapshot.taken_at <= _served_before(constants, now))
        .order_by(MapSnapshot.taken_at.desc())
        .limit(1)
    )


async def prune(session: AsyncSession, constants: Constants, now: datetime) -> int:
    """Drop every snapshot older than the one being served: nothing reads them."""
    current = await served(session, constants, now)
    if current is None:
        return 0
    gone = await session.execute(delete(MapSnapshot).where(MapSnapshot.taken_at < current.taken_at))
    return int(gone.rowcount or 0)
