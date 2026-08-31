# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: the shape of the hull -- its name and where its rooms stand (D-240).

Two things the owner decides about a ship that change nothing in physics: what
it is called, and how its compartments are drawn on the ship's own map.

## Why a ship's rooms may be moved and a city's may not

A place is given once and never recomputed (D-237), and the whole worth of that
rule is that the map is the same map for everybody and the same one tomorrow:
"the mine is north of the gate" is a sentence one can say only where north
holds still.

A ship's interior is on nobody's map but its own (D-201): from the pier a hull
is one node, and what is joined to what past the gangway is exactly what the
single connector hides. So there is no shared north to break here and no
neighbour to disagree with -- only the owner, laying out rooms they alone see.
Hence the exception, and hence its boundary: `places.move` refuses anything
that is not aboard, so the exception cannot leak onto ground.

## The grid is help, not a graph

Rooms snap to a grid of `runtime.SHIP_GRID`, and that is all the grid does. The
edges stay the edges laid at construction -- a compartment is joined for ever
to the one it was laid from (D-201) -- so an arrangement can never cut a hull
in two, strand a room or forge a second way in. A cell is the width two nodes
may never be nearer than, so a tidy hull is also a legible one.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import events, places, travel
from src.engine.ship._base import NotAboard, NotYours, ShipError
from src.engine.ship.belonging import aboard_of, nodes_of
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.ship import Ship
from src.models.world import Node
from src.runtime import SHIP_GRID, SHIP_GRID_REACH, SHIP_NAME_LIMIT


class BadName(ShipError):
    """The name is empty or longer than a name fits in."""


class OffTheGrid(ShipError):
    """The cell asked for is not a cell, or is further out than the map is drawn."""


async def _mine(session: AsyncSession, body: Body, ship: Ship) -> None:
    """Whose ship, and is its owner standing in it.

    Not `_commanded_by`: a nameplate and a floor plan are not orders to a ship
    (D-230 asks for the console for casting off and the passage, and for those
    only). What they do ask for is presence -- matter and the shape of matter
    require it (D-044) -- and ownership.
    """
    if body.state is not BodyState.ALIVE:
        raise ShipError(key="ship-arrange-dead")
    await travel.require_here(session, body)
    if ship.owner_identity_id != body.identity_id:
        raise NotYours(key="ship-not-yours")
    standing = await aboard_of(session, body)
    if standing is None or standing.id != ship.id:
        raise NotAboard(key="ship-arrange-from-aboard")


async def rename(session: AsyncSession, body: Body, ship: Ship, name: str) -> Ship:
    """Name the ship. The nameplate is nailed on the spot, like a plot's (D-178).

    The name is the owner's and the engine makes nothing of it: no route, no
    price and no right hangs on it.
    """
    await _mine(session, body, ship)
    title = name.strip()
    if not title:
        raise BadName(key="ship-no-name")
    if len(title) > SHIP_NAME_LIMIT:
        raise BadName(key="ship-name-too-long", limit=SHIP_NAME_LIMIT)

    was, ship.name = ship.name, title
    #: The group's delegate node carries the ship's name on the space layer:
    #: that is the hull as the sky sees it, and it must not go on saying the
    #: old one. The rooms keep their own names -- «Рубка» is not the ship.
    delegate = await session.get(Node, ship.node_id)
    if delegate is not None:
        delegate.name = title
    await session.flush()

    await events.record(
        session,
        EventKind.SHIP_RENAMED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        ship_id=str(ship.id),
        was=was,
        now=title,
    )
    return ship


def _cell(value: object) -> int:
    """One coordinate of a cell: a whole number, and inside the drawn field.

    Whole, and not rounded into one: a half-cell is a client that has not
    snapped, and snapping it here would quietly move a room somewhere its owner
    did not put it.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise OffTheGrid(key="ship-cell-whole-number")
    cell = int(value)
    if cell != value:
        raise OffTheGrid(key="ship-cell-not-fractional")
    if abs(cell) > SHIP_GRID_REACH:
        raise OffTheGrid(key="ship-cell-off-the-grid", cell=cell, reach=SHIP_GRID_REACH)
    return cell


async def arrange(
    session: AsyncSession, body: Body, ship: Ship, spots: dict[str, tuple[object, object]]
) -> int:
    """Put the ship's rooms where the owner wants them. Returns how many moved.

    `spots` is the node's key -> its cell, and the whole arrangement arrives at
    once: a hull is laid out as a shape, and half an arrangement is a shape
    nobody asked for. Nothing about the graph changes -- the rooms stay joined
    exactly as they were laid.
    """
    await _mine(session, body, ship)
    rooms = {room.key: room for room in await nodes_of(session, ship)}
    if not spots:
        raise OffTheGrid(key="ship-nothing-to-arrange")

    wanted: dict[str, tuple[int, int]] = {}
    for key, cell in spots.items():
        if key not in rooms:
            raise NotAboard(key="ship-no-such-node", node=key)
        if not isinstance(cell, list | tuple):
            raise OffTheGrid(key="ship-cell-is-a-pair")
        try:
            across, along = cell
        except ValueError:
            raise OffTheGrid(key="ship-cell-is-a-pair-of-two") from None
        wanted[key] = (_cell(across), _cell(along))

    #: Two rooms on one cell would be two rooms drawn on top of each other, and
    #: the grid exists precisely so that cannot happen by hand. Asked of the
    #: whole hull, not of the moved half: an untouched room holds its cell too.
    standing: dict[str, tuple[int, int]] = dict(wanted)
    for key, room in rooms.items():
        cell = _at(room)
        if key not in standing and cell is not None:
            standing[key] = cell
    seen: dict[tuple[int, int], str] = {}
    for key, cell in standing.items():
        if cell in seen:
            raise OffTheGrid(key="ship-cell-taken", first=seen[cell], second=key)
        seen[cell] = key

    moved = 0
    for key, (x, y) in wanted.items():
        room = rooms[key]
        if _at(room) == (x, y):
            continue
        await places.move(session, room, (x * SHIP_GRID, y * SHIP_GRID))
        moved += 1
    if moved:
        await events.record(
            session,
            EventKind.SHIP_ARRANGED,
            actor_identity_id=body.identity_id,
            node_id=body.node_id,
            ship_id=str(ship.id),
            moved=moved,
        )
    return moved


def _at(room: Node) -> tuple[int, int] | None:
    """Which cell a room stands in now, or None if it stands off the grid.

    A hull laid before D-240 has its rooms wherever the seating put them, and
    those are not cells. Such a room simply has no cell until it is moved --
    it is not in anybody's way, and rounding it into one would move it without
    being asked.
    """
    point = places.place_of(room)
    if point is None:
        return None
    x, y = point[0] / SHIP_GRID, point[1] / SHIP_GRID
    if x != int(x) or y != int(y):
        return None
    return int(x), int(y)
