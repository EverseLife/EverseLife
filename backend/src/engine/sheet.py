# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The map as a thing (D-319 item 6, OQ-145): memory forgets, a map keeps.

A blank sheet is made like any gear; **drawing** on it copies the drawer's
memory of places onto it as of that moment, for `map.draw_stamina`. From
then on the sheet holds its places for as long as it exists: carried, they
are shown on the globe in the tone of memory with the day it was drawn;
reading it copies nothing back into memory -- else it would only be
forgotten again -- and walking to a place remembers it as ever. What is
drawn is the drawer's memory and nothing more, and a drawn sheet passes
from hand to hand, not over the counter (D-132: coordinates go on trust;
D-319 addendum of 2026-09-06).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import events, memory, travel, world
from src.engine.errors import Refusal
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.sheet import MapSheet

#: The thing a map is drawn on (D-251 id).
SHEET = "map_sheet"


class SheetError(Refusal):
    pass


async def draw(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    item: Item,
    *,
    now: datetime,
) -> MapSheet:
    """Draw the body's memory of places onto a blank sheet in its hands."""
    if body.state is not BodyState.ALIVE:
        raise SheetError(key="map-sheet-dead-draws")
    await travel.require_here(session, body)
    if item.type_key != SHEET:
        raise SheetError(key="map-sheet-not-a-sheet")
    #: The body's row is taken and **reread** before anything that decides
    #: the payment -- whether the sheet is drawn on, and what strength is
    #: left. Two hands of one identity over two sheets would otherwise both
    #: read the same reserve and both pay out of it (CLAUDE.md: stamina is
    #: money). `refresh` rather than `get`: a body already loaded in this
    #: session keeps its stale attributes past a plain `FOR UPDATE`.
    await session.flush()
    await session.refresh(body, with_for_update=True)
    if await session.get(MapSheet, item.id) is not None:
        raise SheetError(key="map-sheet-drawn")
    places = sorted(await memory.known(session, body.identity_id))
    if not places:
        raise SheetError(key="map-sheet-empty")
    spend = float(constants[R.MAP_DRAW_STAMINA])
    if spend > float(body.stamina):
        raise SheetError(key="map-sheet-no-strength", need=spend, have=float(body.stamina))
    body.stamina = Decimal(str(float(body.stamina) - spend))
    sheet = MapSheet(item_id=item.id, drawn_at=now, places=places)
    session.add(sheet)
    await session.flush()
    #: The journal says what became of the sheet, and the push tells the
    #: hands and the map to look again (D-226).
    await events.record(
        session,
        EventKind.MAP_DRAWN,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(item.id),
        places=len(places),
    )
    return sheet


async def held(session: AsyncSession, body: Body) -> dict[str, datetime]:
    """The places on the sheets in the body's hands, each with the latest
    moment it was drawn at: what the map shows from the pocket."""
    pocket = await world.body_container(session, body)
    rows = (
        await session.execute(
            select(MapSheet)
            .join(Item, Item.id == MapSheet.item_id)
            .where(Item.container_id == pocket.id)
        )
    ).scalars()
    out: dict[str, datetime] = {}
    for sheet in rows:
        for key in sheet.places:
            known = out.get(key)
            if known is None or known < sheet.drawn_at:
                out[key] = sheet.drawn_at
    return out


async def drawn(session: AsyncSession, item_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Which of these items are drawn sheets: the inventory tells a blank from a map."""
    if not item_ids:
        return set()
    rows = await session.execute(select(MapSheet.item_id).where(MapSheet.item_id.in_(item_ids)))
    return set(rows.scalars())
