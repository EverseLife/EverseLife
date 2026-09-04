# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The lines: which vessels a machine drinks from and pours into (D-288).

A machine that eats or gives a liquid has **ports** -- what its recipe or its
class takes and gives: fuel for an engine, oxygen for the life support -- and
a line is one vessel standing on one port, in a chosen order. A port with no
line at all takes any suitable vessel of the hull; the rows here are the
owner's narrowing of that, never a requirement.

Keyed by the machine and the vessel **items**, not by rooms: the hull is one
building (D-288), and a line reaches across every compartment of it. A
dismantled machine or vessel takes its rows along (CASCADE); one merely taken
down keeps them and simply stops answering -- `ship.lines.sources` reads only
what stands aboard now, so a stale row is a memory, not a leak, and the same
vessel put back stands on its line again.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class FeedLine(Base):
    """One vessel on one port of one machine, and where it stands in the order."""

    __tablename__ = "feed_line"
    __table_args__ = (
        UniqueConstraint("machine_item_id", "port", "vessel_item_id", name="uq_feed_line"),
        Index("ix_feed_line_machine", "machine_item_id", "port"),
        Index("ix_feed_line_vessel", "vessel_item_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    machine_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("item.id", ondelete="CASCADE"), nullable=False
    )
    #: The port's name on the machine (`ship.lines.Port.name`): `fuel`,
    #: `oxygen`. A key of the schema, never a word of the locale.
    port: Mapped[str] = mapped_column(nullable=False)
    vessel_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("item.id", ondelete="CASCADE"), nullable=False
    )
    #: The order the port drinks or fills in, counted from nought.
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = created_column()
