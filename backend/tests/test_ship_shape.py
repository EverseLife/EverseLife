# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The shape of a hull: its name and where its rooms stand (D-240).

Two things the owner decides that change nothing in physics, and one number
that changes what walking a ship feels like:

* a **step across a hull is one second**, the same for every pair of rooms: a
  ship is a room one walks through, not ground one crosses;
* the rooms are **arranged by hand** on the ship's own map. That is an exception
  to D-237, and the test pins down its boundary: ground does not move, only a
  node aboard, and the graph is untouched by any arrangement;
* the ship is **renamed** by its owner, and by nobody else.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import places, ship, travel, world
from src.models.estate import Building
from src.models.identity import Body
from src.models.job import JobState
from src.models.ship import Ship
from src.models.world import Node, Planet
from src.runtime import SHIP_GRID, SHIP_GRID_REACH


async def _port(session: AsyncSession) -> Node:
    node = await world.create_node(
        session, f"terra.port.{uuid.uuid4().hex[:8]}", "Космодром", area_m2=400, planet=Planet.TERRA
    )
    session.add(Building(node_id=node.id, area_m2=400))
    await session.flush()
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "space_shipyard", quality=60, origin="тест")
    return node


async def _hull(
    session: AsyncSession, constants: Constants, port: Node, *, rooms: int = 1
) -> tuple[Ship, Body]:
    identity = await world.create_identity(session, f"Корабел-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, port)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "ship_node_foundation", amount=rooms, origin="тест")

    job = await ship.found(session, constants, body, "Заря")
    await ship.keel_laid(session, job)
    await _done(session, job)
    vessel = (await ship.ships_of(session, identity.id))[-1]
    body.node_id = vessel.connector_node_id
    await session.flush()
    for _ in range(rooms - 1):
        more = await ship.extend(session, constants, body)
        await ship.keel_laid(session, more)
        await _done(session, more)
    return vessel, body


async def _done(session: AsyncSession, job) -> None:
    """Close a keel run by hand: left pending it goes on occupying the hands."""
    job.state = JobState.DONE
    job.finished_at = job.run_at
    await session.flush()


async def test_a_step_across_a_hull_is_one_second(
    session: AsyncSession, constants: Constants
) -> None:
    """The same for every pair, and it is the vault's number rather than a literal."""
    port = await _port(session)
    vessel, _ = await _hull(session, constants, port, rooms=3)
    rooms = await ship.nodes_of(session, vessel)

    walked = 0
    for one in rooms:
        for other in rooms:
            edge = await travel._edge_between(session, one.id, other.id)
            if edge is None:
                continue
            assert edge.base_seconds == int(constants[R.SHIP_STEP_SECONDS])
            walked += 1
    assert walked, "коридоры между отсеками нашлись"


async def test_the_owner_arranges_the_rooms_and_the_graph_does_not_move(
    session: AsyncSession, constants: Constants
) -> None:
    """Dragging changes the drawing. What is joined to what was decided at the keel."""
    port = await _port(session)
    vessel, body = await _hull(session, constants, port, rooms=2)
    rooms = await ship.nodes_of(session, vessel)
    before = await travel._edge_between(session, rooms[0].id, rooms[1].id)
    assert before is not None

    moved = await ship.arrange(session, body, vessel, {rooms[1].key: (2, -1)})
    assert moved == 1
    assert places.place_of(rooms[1]) == (2 * SHIP_GRID, -1 * SHIP_GRID)

    after = await travel._edge_between(session, rooms[0].id, rooms[1].id)
    assert after is not None and after.id == before.id, "ребро то же самое"


async def test_two_rooms_never_share_a_cell(session: AsyncSession, constants: Constants) -> None:
    """The grid exists precisely so that cannot happen by hand."""
    port = await _port(session)
    vessel, body = await _hull(session, constants, port, rooms=2)
    rooms = await ship.nodes_of(session, vessel)
    await ship.arrange(session, body, vessel, {rooms[1].key: (3, 0)})

    with pytest.raises(ship.OffTheGrid):
        await ship.arrange(session, body, vessel, {rooms[0].key: (3, 0)})


async def test_a_cell_off_the_drawing_is_refused(
    session: AsyncSession, constants: Constants
) -> None:
    """A bound on the picture, not on the ship -- and a half-cell is a client bug."""
    port = await _port(session)
    vessel, body = await _hull(session, constants, port)
    rooms = await ship.nodes_of(session, vessel)

    with pytest.raises(ship.OffTheGrid):
        await ship.arrange(session, body, vessel, {rooms[0].key: (SHIP_GRID_REACH + 1, 0)})
    with pytest.raises(ship.OffTheGrid):
        await ship.arrange(session, body, vessel, {rooms[0].key: (0.5, 0)})


async def test_ground_does_not_move(session: AsyncSession, constants: Constants) -> None:
    """The one exception is a hull, and `places.move` is where it is kept (D-237)."""
    port = await _port(session)
    with pytest.raises(places.PlaceIsFixed):
        await places.move(session, port, (0.0, 0.0))


async def test_somebody_elses_hull_is_not_rearranged(
    session: AsyncSession, constants: Constants
) -> None:
    """A guest aboard reads the plan and moves nothing."""
    port = await _port(session)
    vessel, _ = await _hull(session, constants, port)
    rooms = await ship.nodes_of(session, vessel)
    stranger_id = await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:6]}")
    guest = await world.print_body(session, stranger_id, rooms[0])

    with pytest.raises(ship.NotYours):
        await ship.arrange(session, guest, vessel, {rooms[0].key: (1, 1)})
    with pytest.raises(ship.NotYours):
        await ship.rename(session, guest, vessel, "Чужая")


async def test_the_hull_is_renamed_by_its_owner(
    session: AsyncSession, constants: Constants
) -> None:
    """The nameplate is nailed on the spot, and the sky learns the new name too."""
    port = await _port(session)
    vessel, body = await _hull(session, constants, port)

    await ship.rename(session, body, vessel, "  Полярная  ")
    assert vessel.name == "Полярная"
    delegate = await session.get(Node, vessel.node_id)
    assert delegate is not None and delegate.name == "Полярная"

    with pytest.raises(ship.BadName):
        await ship.rename(session, body, vessel, "   ")
