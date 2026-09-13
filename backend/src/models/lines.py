# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The lines: which vessels a machine drinks from and pours into (D-288).

A machine that eats or gives a liquid has **ports** -- what its recipe or its
class takes and gives: fuel for an engine, oxygen for the life support, water
in and oxygen and hydrogen out for the electrolyser (D-340) -- and a line is
one vessel standing on one port, in a chosen order. A port with no line at
all reaches nothing (D-288 as amended 2026-09-04): the rows here are the whole
of what a port reaches, never a narrowing of some default.

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
    #: `oxygen`, `water`, `hydrogen`, `lube`. A key of the schema, never a
    #: word of the locale.
    port: Mapped[str] = mapped_column(nullable=False)
    vessel_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("item.id", ondelete="CASCADE"), nullable=False
    )
    #: The order the port drinks or fills in, counted from nought.
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = created_column()


class VesselName(Base):
    """The name the owner gave a vessel on the lines (D-288, D-340):
    «Кислород, левый борт» in place of «Бак 2».

    A table of its own rather than a column on `item`: a name is plumbing, not
    the thing's identity -- `item` rows are copied, split and folded by every
    stack path in the world, and none of them should have to know about it.
    Keyed by the vessel, so the name stays with the tank taken down and put
    back, exactly as its lines do, and goes with it when it is dismantled.
    """

    __tablename__ = "vessel_name"

    vessel_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("item.id", ondelete="CASCADE"), primary_key=True
    )
    #: The owner's own words: the engine makes nothing of them.
    name: Mapped[str] = mapped_column(nullable=False)

    created_at: Mapped[datetime] = created_column()
