# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Where a station built in place may go up (D-268, D-312, D-338).

A station built in place stands where it is made, so the batch that makes
it **is** the putting up, and every door the putting up would pass is asked
of the batch -- before the hours, not after them: twenty hours and two steel
frames are not a thing to spend on a refusal. Split off `_internal` when it
reached the eight hundred lines the quality bar allows.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine.biome import on_ice
from src.engine.craft._base import CraftError
from src.engine.vent import FLARE_CLASS
from src.engine.world import BIOPRINTER, station_names
from src.models.identity import Body
from src.models.world import Node, is_aboard


async def require_place(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    output: str,
    units: float,
) -> None:
    """Refuse a batch whose station could not stand where the master stands."""
    if not catalog.recipes.built(output):
        return
    where = await session.get(Node, body.node_id)
    if where is None:  # pragma: no cover -- a body without a node is a bug
        raise CraftError(key="craft-body-off-node")
    #: Nothing is built on ice (D-338): the cap creeps and cracks under a
    #: footing, and a station is a footing like a house.
    if on_ice(constants, where):
        raise CraftError(key="craft-build-on-ice", goods=output)
    #: A flare stack burns vent gas under a sky with air, and a hull has none
    #: (D-340): a sealed hull lets the gas out, one under a sky keeps it in the
    #: vessels on its line. Made aboard, it would stand and serve nothing.
    if output in station_names(FLARE_CLASS) and is_aboard(where):
        raise CraftError(key="craft-flare-aboard", goods=output)
    #: Making a bioprinter in a city **is** putting one up there, and the door
    #: it must pass is the same one (D-312).
    if output in station_names(BIOPRINTER):
        from src.engine import station as gate  # noqa: PLC0415 -- lazy: cycle with station

        await gate.require_printer_room(session, body, where)
        #: And one at a time: the door is asked once for the batch, so a batch
        #: of two would pass it once and stand two (D-312). Outside a city
        #: nothing is refused -- there a printer is just a machine.
        from src.engine import city as town  # noqa: PLC0415 -- lazy: cycle with city

        if units > 1 and await town.of_node(session, where) is not None:
            raise CraftError(key="craft-one-printer-at-a-time")
