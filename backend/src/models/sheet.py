# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A map as a thing (D-319 item 6, OQ-145): memory forgets, a map keeps.

A sheet is an ordinary item -- made, carried, traded, lost -- and this row is
what is drawn on it: the places its drawer remembered on the day of drawing.
The row lives and dies with the item; there is no map without the sheet.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class MapSheet(Base):
    __tablename__ = "map_sheet"

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("item.id", ondelete="CASCADE"), primary_key=True
    )
    #: The moment the sheet was drawn: the map shows each place with the day
    #: of that moment in its own planet's calendar (`climate.day_index`), so a
    #: sheet drawn on one planet reads right on another.
    drawn_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The keys of the surface nodes the drawer remembered.
    places: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
