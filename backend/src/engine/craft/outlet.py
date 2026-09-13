# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""craft: where a batch's liquids go, and how much room they find there (D-340).

A master is not a machine and has no backlog, so the room on a batch's
outlet is **not reserved**: aboard it is checked at the door
(`plumbing.require_room`), on the ground not even that, and somebody else may
fill the tank during the hours -- then the surplus spills at the finish, with
an event. The owner accepted that on one condition: the player sees it. Before
the start and on the running batch, the window says where each liquid of the
batch goes, how much room there is now against what the batch will give, and
-- when the room is short -- that the surplus will spill.

Said here and nowhere else, so the forecast and the running batch cannot
disagree about the place, and the place is the one the finish pours into:
aboard the vessels on the port's line; on the ground the vessels in the
master's hands while the master stands at the machine, then those standing at
it. A vent gas says where it goes past its vessels (`engine.vent`), and only
under a sky with air aboard -- where its line is its one place -- has a room
to fall short of.

A read: nothing is locked and nothing is made. What the batch will give is not
repeated here -- the client has the batch's size and the recipe's byproduct
(D-225) -- only what it cannot see: the place and the room.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import liquid, vent, world
from src.engine.ship import lines
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.world import Node
from src.units import amount, amount_float

#: Where a liquid of a batch goes -- keys of the wire, never words. Into the
#: vessels on the port's line, aboard...
LINE = "line"
#: ... into the vessels in the master's hands and at the machine, on the ground...
REACH = "reach"
#: ... or only at the machine: the master is not standing at it.
PLACE = "place"
#: A vent gas aboard a sealed hull: into the vessels on its line, the rest
#: overboard -- nothing to fall short of, but not "all of it out" either.
OVERBOARD = "overboard"

#: `outlets` was not handed the plumbing, and reads it itself.
_UNREAD = object()


async def outlets(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    *,
    node: Node,
    machine: Item | None,
    output: str,
    body: Body | None,
    plumbed: lines.Plumbing | None | object = _UNREAD,
) -> list[dict[str, object]]:
    """The places a batch of `output` at this machine pours into, for the window.

    One row per liquid: `goods`, `where` -- `line`, `reach` or `place` with the
    `room` they have now, in units of that liquid; a vent gas with a way out of
    the place says `void`, `overboard` or `flare` and has no room to fall short
    of. Empty for a batch that gives no liquid at all. `plumbed` is the
    machine's plumbing when the caller has read it already (the forecast has).
    """
    book = catalog.recipes
    gases = vent.gases_of(catalog, output)
    if not book.is_liquid(output) and not gases:
        return []
    if plumbed is _UNREAD:
        plumbed = await lines.plumbing_of(session, constants, catalog, machine, output)
    assert plumbed is None or isinstance(plumbed, lines.Plumbing)
    rows: list[dict[str, object]] = []
    if book.is_liquid(output):
        if plumbed is not None:
            where, vessels = LINE, plumbed.outlets.get(output, [])
        else:
            #: The finish pours into the hands only while the master stands at
            #: the machine (`batch.finish`), and that is what is shown.
            at_bench = (
                body is not None and body.state is BodyState.ALIVE and body.node_id == node.id
            )
            where, vessels = await _reach(session, catalog, node, body if at_bench else None)
        room = await liquid.room_seen(session, catalog, vessels, output)
        rows.append({"goods": output, "where": where, "room": amount_float(amount(room))})
    if gases:
        way = await vent.sink(session, node)
        for name in gases:
            if way is not None:
                lined = plumbed is not None and way == vent.VOID and plumbed.vents.get(name)
                rows.append({"goods": name, "where": OVERBOARD if lined else way})
            elif plumbed is not None:
                room = await liquid.room_seen(session, catalog, plumbed.vents.get(name, []), name)
                rows.append({"goods": name, "where": LINE, "room": amount_float(amount(room))})
    return rows


async def _reach(
    session: AsyncSession, catalog: Catalog, node: Node, body: Body | None
) -> tuple[str, list[Item]]:
    """The vessels a liquid of a batch on the ground pours into, in the
    finish's order: the master's hands first when the master is at the
    machine, then what stands in the place. Read, never made."""
    vessels: list[Item] = []
    if body is not None:
        vessels.extend(
            await liquid.vessels_in(session, catalog, await world.body_container(session, body))
        )
    yard = await world.node_yard(session, node)
    if yard is not None:
        vessels.extend(await liquid.vessels_in(session, catalog, yard))
    return (REACH if body is not None else PLACE), vessels
