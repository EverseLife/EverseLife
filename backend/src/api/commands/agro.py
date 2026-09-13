# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The field automaton: setting it, stopping it and reading it (D-339)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.common import _alive, _alive_read
from src.api.registry import Refused, command
from src.constants import current, current_catalog
from src.engine import agro
from src.models.inventory import Item


def _item_id(message: dict, key: str) -> uuid.UUID | None:
    raw = message.get(key)
    if raw is None:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError as wrong:
        raise Refused(key="cmd-no-such-item") from wrong


async def _machine(db: AsyncSession, message: dict) -> Item:
    machine_id = _item_id(message, "machine")
    item = None if machine_id is None else await db.get(Item, machine_id)
    if item is None:
        raise Refused(key="cmd-no-such-item")
    return item


@command("agro.program")
async def _agro_program(state: dict, db: AsyncSession, message: dict) -> dict:
    """Set a field automaton whole: programme, plots, and the three storages."""
    body = await _alive(state, db)
    item = await _machine(db, message)
    await agro.program(
        db,
        current(),
        current_catalog(),
        body,
        item,
        steps=message.get("program"),
        plots=message.get("plots", []),
        seeds=_item_id(message, "seeds"),
        fertilizer=_item_id(message, "fertilizer"),
        harvest=_item_id(message, "harvest"),
    )
    #: A confirmation, not the state: what the machine does with it arrives
    #: with the tick, and `agro.view` reads it back (D-225).
    return {"item": str(item.id), "programmed": True}


@command("agro.stop")
async def _agro_stop(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take the programme off. The machine stays; the beds go on without it."""
    body = await _alive(state, db)
    item = await _machine(db, message)
    stopped = await agro.stop(db, current(), current_catalog(), body, item)
    return {"item": str(item.id), "stopped": stopped}


@command("agro.view", readonly=True)
async def _agro_view(state: dict, db: AsyncSession, message: dict) -> dict:
    """The field automatons set in this yard. A read."""
    body = await _alive_read(state, db)
    return await agro.view(db, body)
