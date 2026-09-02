# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The road a word travels (D-222): the planet's graph cached in memory,
forgotten when an eruption tears an edge, and the delay between two ends
of a letter.
"""

from __future__ import annotations

import heapq
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import ship, travel
from src.models.world import Edge, Node, Planet
from src.runtime import (
    NET_GRAPH_TTL,
    NET_REACH_CACHE,
)
from src.units import HOURS_PER_DAY, SECONDS_PER_HOUR

# --- the road ----------------------------------------------------------------

Adjacency = dict[uuid.UUID, list[tuple[uuid.UUID, float]]]


@dataclass(slots=True)
class Graph:
    loaded_at: datetime
    edges: Adjacency
    planets: dict[uuid.UUID, Planet]


_graph: Graph | None = None

#: Distance maps by source node, valid for the graph above. Oldest out first.
_reach: OrderedDict[uuid.UUID, dict[uuid.UUID, float]] = OrderedDict()


def forget_graph() -> None:
    """Drop the map in memory: an edge appeared or went. The TTL does the same
    for another process."""
    global _graph
    _graph = None
    _reach.clear()


async def _load(session: AsyncSession, constants: Constants, now: datetime) -> Graph:
    global _graph
    if _graph is not None and now - _graph.loaded_at < NET_GRAPH_TTL:
        return _graph
    edges: Adjacency = {}
    for edge in (await session.execute(select(Edge))).scalars():
        seconds = travel.edge_seconds(constants, edge)
        edges.setdefault(edge.node_a_id, []).append((edge.node_b_id, seconds))
        edges.setdefault(edge.node_b_id, []).append((edge.node_a_id, seconds))
    planets = {
        node_id: planet
        for node_id, planet in (await session.execute(select(Node.id, Node.planet))).all()
    }
    _graph = Graph(loaded_at=now, edges=edges, planets=planets)
    _reach.clear()
    return _graph


def _from(graph: Graph, source: uuid.UUID) -> dict[uuid.UUID, float]:
    """Seconds from one node to every node it reaches. Dijkstra, once per source."""
    known = _reach.get(source)
    if known is not None:
        _reach.move_to_end(source)
        return known
    best: dict[uuid.UUID, float] = {source: 0.0}
    queue: list[tuple[float, bytes]] = [(0.0, source.bytes)]
    while queue:
        cost, raw = heapq.heappop(queue)
        here = uuid.UUID(bytes=raw)
        if cost > best[here]:
            continue
        for neighbour, seconds in graph.edges.get(here, ()):
            step = cost + seconds
            if step < best.get(neighbour, float("inf")):
                best[neighbour] = step
                heapq.heappush(queue, (step, neighbour.bytes))
    _reach[source] = best
    while len(_reach) > NET_REACH_CACHE:
        _reach.popitem(last=False)
    return best


async def road_seconds(
    session: AsyncSession,
    constants: Constants,
    here: uuid.UUID,
    there: uuid.UUID,
    *,
    now: datetime,
) -> float:
    """How long the road between two nodes takes, seconds (D-222)."""

    if here == there:
        return 0.0
    graph = await _load(session, constants, now)
    sea = (
        float(constants[R.SHIP_ASCENT_HOURS]) + float(constants[R.SHIP_DESCENT_HOURS])
    ) * SECONDS_PER_HOUR
    planet_a = graph.planets.get(here)
    planet_b = graph.planets.get(there)
    if planet_a is None or planet_b is None:
        return sea
    if planet_a is not planet_b:
        #: A word between worlds goes with the cheap passage (D-271): the
        #: least delta-v the sky offers today, and however long that arc takes.
        #: No orbits to ask -- the slow end of the slider. Not knowing the sky
        #: must never come out cheaper than knowing it.
        curve = await ship.passage_curve(
            session, constants, planet_a, planet_b, at=now, flybys=False
        )
        cheapest = ship.course.cheapest(curve)
        hours = (
            float(constants[R.ORBIT_LONGEST_DAYS]) * HOURS_PER_DAY
            if cheapest is None
            else cheapest.hours
        )
        return float(hours) * SECONDS_PER_HOUR
    seconds = _from(graph, here).get(there)
    return sea if seconds is None else seconds


async def delay_between(
    session: AsyncSession,
    constants: Constants,
    here: uuid.UUID | None,
    there: uuid.UUID | None,
    *,
    now: datetime,
) -> timedelta:
    """The delay of a letter from one node to another. No node on either end
    -- nobody to measure to -- is no delay."""
    if here is None or there is None:
        return timedelta(0)
    seconds = await road_seconds(session, constants, here, there, now=now)
    return timedelta(seconds=seconds * float(constants[R.COMM_DELAY_PER_SECOND]))
