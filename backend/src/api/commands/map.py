# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The map as a thing over the socket (D-319 item 6): draw one."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.common import _alive, _own_item
from src.api.registry import command
from src.constants import current
from src.engine import sheet


@command("map.draw")
async def _map_draw(state: dict, db: AsyncSession, message: dict) -> dict:
    """Draw the body's memory of places onto a blank sheet in its hands.

    The answer confirms the drawing and counts the places; the sheet's
    places appear on the map with the next look (D-226).
    """
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    drawn = await sheet.draw(db, current(), body, item, now=datetime.now(UTC))
    return {"item": str(item.id), "places": len(drawn.places), "day": drawn.drawn_day}
