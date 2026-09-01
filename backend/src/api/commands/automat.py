# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The automats: programming and reading the factory floor (D-253).

The chains between machines are wave 5 (the node editor); today a machine
takes one recipe, and these three commands are all the floor speaks.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.common import _alive, _alive_read
from src.api.registry import Refused, command
from src.constants import current, current_catalog
from src.engine import automat
from src.models.inventory import Item


@command("auto.program")
async def _auto_program(state: dict, db: AsyncSession, message: dict) -> dict:
    """Load a recipe into an automat standing here. Out of own knowledge (D-253)."""
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["machine"]))
    if item is None:
        raise Refused(key="cmd-no-such-item")
    row = await automat.program(db, current(), current_catalog(), body, item, message["recipe"])
    #: A confirmation, not the state (the quality bar): what the floor makes
    #: of it arrives with the next tick, and `auto.view` reads it. The item id
    #: is what the client addressed us by; everything else it already has (D-225).
    return {"item": str(item.id), "recipe": row.recipe_key}


@command("auto.stop")
async def _auto_stop(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take the programme off. The machine stays; the recipe goes."""
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["machine"]))
    if item is None:
        raise Refused(key="cmd-no-such-item")
    await automat.stop(db, current(), body, item)
    return {"item": str(item.id), "stopped": True}


@command("auto.view", readonly=True)
async def _auto_view(state: dict, db: AsyncSession, message: dict) -> dict:
    """The automats standing here: machine, programme, backlog. A read."""
    body = await _alive_read(state, db)
    return {"automats": await automat.view(db, current_catalog(), body)}
