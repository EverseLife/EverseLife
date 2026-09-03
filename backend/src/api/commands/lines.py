# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The lines of a hull: which vessels a machine drinks from (D-288).

Two commands, and the whole picture is read by one of them: the owner plumbs
a port with `line.set`, the console reads every machine and vessel of the
hull with `line.view` and draws the rest itself.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.common import _alive, _alive_read
from src.api.commands.transport import _ship_of
from src.api.registry import Refused, command
from src.constants import current, current_catalog
from src.engine import ship
from src.models.inventory import Item


async def _item(db: AsyncSession, asked: object) -> Item:
    try:
        found = await db.get(Item, uuid.UUID(str(asked)))
    except ValueError as bad:
        raise Refused(key="cmd-no-such-item") from bad
    if found is None:
        raise Refused(key="cmd-no-such-item")
    return found


@command("line.set")
async def _line_set(state: dict, db: AsyncSession, message: dict) -> dict:
    """Plumb one port of one machine: `vessels` in the order they are drunk
    from; an empty list puts the port back to "any aboard"."""
    body = await _alive(state, db)
    vessel = await _ship_of(db, body, message.get("ship"))
    machine = await _item(db, message.get("machine"))
    asked = message.get("vessels") or []
    if not isinstance(asked, list):
        raise Refused(key="cmd-no-such-item")
    port = str(message.get("port") or "")
    count = await ship.set_lines(
        db,
        current(),
        current_catalog(),
        body,
        vessel,
        machine,
        port,
        [await _item(db, one) for one in asked],
    )
    #: A confirmation, not the state (the quality bar): the picture arrives
    #: with `line.view`, reread on the event.
    return {"item": str(machine.id), "port": port, "vessels": count}


@command("line.view", readonly=True)
async def _line_view(state: dict, db: AsyncSession, message: dict) -> dict:
    """The hull's plumbing: machines with ports, vessels, and the lines. A read."""
    body = await _alive_read(state, db)
    vessel = await _ship_of(db, body, message.get("ship"))
    return await ship.lines_view(db, current(), current_catalog(), vessel)
