# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Where a node stands on the map, once and for everybody (D-237).

Checked is what the rule exists for:

* a node has a place from the moment it is created, and next to what it was
  laid from -- so an edge on the map is short and the graph reads as a graph;
* nobody moves. Neither a neighbour appearing, nor a find, nor a second reading
  changes a point that was once given -- the map is eternal, like the world;
* the anchor climbs to the layer being drawn: a find beyond the walls stands
  beside the whole city, because that is what a city is on a planet's map;
* two nodes never sit on top of each other;
* the space layer keeps no places: a planet's point comes from the clock.
"""

from __future__ import annotations

import math
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import places, travel, world
from src.models.world import Layer, Node, Planet, Surface
from src.runtime import MAP_MIN_GAP, MAP_STEP


async def _node(
    session: AsyncSession,
    name: str,
    *,
    layer: Layer = Layer.CITY,
    parent: Node | None = None,
    anchor: Node | None = None,
    planet: Planet = Planet.TERRA,
) -> Node:
    return await world.create_node(
        session,
        f"place.{uuid.uuid4().hex}",
        name,
        area_m2=100,
        layer=layer,
        parent=parent,
        anchor=anchor,
        planet=planet,
    )


def _gap(one: Node, other: Node) -> float:
    here, there = places.place_of(one), places.place_of(other)
    assert here is not None and there is not None
    return math.hypot(here[0] - there[0], here[1] - there[1])


async def test_a_node_stands_next_to_what_it_was_laid_from(session: AsyncSession) -> None:
    """One step away, and the step is the same for everybody (D-237)."""
    city = await _node(session, "Город", layer=Layer.PLANET)
    core = await _node(session, "Ядро", parent=city)
    library = await _node(session, "Библиотека", parent=city, anchor=core)

    assert places.place_of(core) == places.ORIGIN, "первый узел группы — её начало"
    assert _gap(core, library) == pytest.approx(MAP_STEP), "второй стоит в шаге от первого"


async def test_nobody_moves_when_the_map_grows(session: AsyncSession) -> None:
    """A place given once is never recomputed: the world has no wipes (D-007)."""
    city = await _node(session, "Город", layer=Layer.PLANET)
    core = await _node(session, "Ядро", parent=city)
    library = await _node(session, "Библиотека", parent=city, anchor=core)
    was = places.place_of(library)

    for number in range(5):
        await _node(session, f"Дом {number}", parent=city, anchor=core)
    assert places.place_of(library) == was, "соседи появились — библиотека не сдвинулась"

    #: And a second assignment is not one: the node keeps what it has.
    await places.assign(session, library, anchor=core)
    assert places.place_of(library) == was


async def test_nodes_never_sit_on_one_another(session: AsyncSession) -> None:
    """A crowd around one node spreads instead of piling up."""
    city = await _node(session, "Город", layer=Layer.PLANET)
    core = await _node(session, "Ядро", parent=city)
    houses = [await _node(session, f"Дом {n}", parent=city, anchor=core) for n in range(12)]

    for i, one in enumerate(houses):
        for other in houses[i + 1 :]:
            assert _gap(one, other) >= MAP_MIN_GAP, "две точки не легли друг на друга"


async def test_a_crowd_past_the_old_ceiling_still_spreads(session: AsyncSession) -> None:
    """Enough nodes round one anchor to fill six rings, and not one pair on top
    of another.

    This is the planet's surface, not an exotic case: every find of one city is
    laid beside the same point, so the crowd round it is however much the world
    has been explored. The seating used to try twelve directions on each of six
    rings and then keep whatever spot it had computed last -- occupied or not --
    and a place is never recomputed (D-007), so that overlap stayed for good.
    """
    planet = await _node(session, "Терра", layer=Layer.SPACE)
    city = await _node(session, "Город", layer=Layer.PLANET, parent=planet)
    crowd = [
        await _node(session, f"Находка {n}", layer=Layer.PLANET, parent=planet, anchor=city)
        for n in range(240)
    ]

    points = [places.place_of(node) for node in crowd] + [places.place_of(city)]
    assert all(point is not None for point in points)
    for i, one in enumerate(points):
        for other in points[i + 1 :]:
            assert math.hypot(one[0] - other[0], one[1] - other[1]) >= MAP_MIN_GAP, (
                "две точки легли друг на друга"
            )


async def test_the_anchor_climbs_to_the_layer_being_drawn(session: AsyncSession) -> None:
    """A find beyond the walls stands beside the city, not beside a gate (D-206, D-237).

    On a planet's map the whole city is one point, so a node sought from inside
    it can only be laid next to that point -- and the gate the road actually
    starts at has no place of its own on that map at all.
    """
    terra = await _node(session, "Терра", layer=Layer.SPACE)
    city = await _node(session, "Столица", layer=Layer.PLANET, parent=terra)
    gate = await _node(session, "Выход из города", parent=city)
    field = await _node(session, "Поле", layer=Layer.PLANET, parent=terra, anchor=gate)

    assert _gap(city, field) == pytest.approx(MAP_STEP), "находка легла в шаге от города"


async def test_the_sky_keeps_no_places(session: AsyncSession) -> None:
    """A planet's point is a function of the clock, and a stored one would argue with it."""
    terra = await _node(session, "Терра", layer=Layer.SPACE)
    assert places.place_of(terra) is None
    assert places.wire(terra) is None


async def test_the_backfill_places_an_old_world_and_moves_nobody(session: AsyncSession) -> None:
    """A world laid before the rule gets its map, and gets it only once.

    The nodes are built by hand here, without `create_node`, precisely because
    that is what a node from before D-237 looks like: no place at all.
    """
    terra = await _node(session, "Терра", layer=Layer.SPACE)
    city = await _node(session, "Столица", layer=Layer.PLANET, parent=terra)
    old = []
    for number in range(4):
        node = Node(
            key=f"old.{uuid.uuid4().hex}",
            name=f"Старый узел {number}",
            planet=Planet.TERRA,
            layer=Layer.CITY,
            parent_id=city.id,
            area_m2=100,
        )
        session.add(node)
        old.append(node)
    await session.flush()
    for one, other in zip(old, old[1:], strict=False):
        await travel.connect(session, one, other, base_seconds=10, surface=Surface.PAVED)

    assert all(places.place_of(node) is None for node in old), "у старых узлов места нет"
    laid = await places.backfill(session)
    assert laid >= len(old)
    assert all(places.place_of(node) is not None for node in old)

    #: The group is walked outwards from its own first node -- age first, key to
    #: break a tie, the same one on every server -- and that node takes the
    #: origin. Reading the order back here rather than assuming it: the four
    #: were created in one transaction and share a stamp to the microsecond.
    order = sorted(old, key=lambda node: (node.created_at, node.key))
    assert places.place_of(order[0]) == places.ORIGIN, "корень группы — её начало"
    #: Walked along the edges, so a neighbour of the root came out a neighbour
    #: on the map too.
    at = old.index(order[0])
    beside = old[at + 1] if at + 1 < len(old) else old[at - 1]
    assert _gap(order[0], beside) == pytest.approx(MAP_STEP), "сосед по ребру — в шаге"

    #: The second run is a no-op: nobody is placed again and nobody moves.
    before = [places.place_of(node) for node in old]
    assert await places.backfill(session) == 0
    assert [places.place_of(node) for node in old] == before
