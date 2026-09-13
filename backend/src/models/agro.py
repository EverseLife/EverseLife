# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The field automaton: a programme of commands over plots (D-120, D-339).

The fourth member of the automat family (D-253) and the third move from
labour to capital, after the automatic station and the rig. A machine
standing in a yard walks its owner's programme -- plough, sow, hold the
moisture, feed in a stage, weed, thin, harvest, lie fallow -- over the
plots given to it, for as long as the yard's vessels hold lubricant and the
pool holds energy. It sees the moisture, the stage and the calendar, and
nothing else: health, weeds and the signs of a pest are the farmer's eyes.

The machine is a storage itself (the bunker, `store` of its recipe), and
its owner names three storages of the yard: where the seeds come from,
where the fertilizer comes from and where the harvest goes. One may be all
three.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class FieldAutomat(Base):
    """A field automaton standing in a node, its programme and where it stands in it."""

    __tablename__ = "field_automat"
    __table_args__ = (
        Index("ix_field_automat_node", "node_id"),
        #: `ON DELETE SET NULL` walks these on every deleted thing -- and stacks
        #: are deleted by the thousand: without an index each is a scan.
        Index("ix_field_automat_seeds", "seeds_item_id"),
        Index("ix_field_automat_fertilizer", "fertilizer_item_id"),
        Index("ix_field_automat_harvest", "harvest_item_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    #: The machine itself: a thing with quality and condition, and a storage.
    #: No foreign key, as with the automat and the rig: the advance finds the
    #: machine gone and takes the row away with it.
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    #: Who programmed it. The energy bill goes here (D-135: whoever burns pays).
    owner_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )

    #: The commands in order, walked in a circle (D-339): rows of
    #: `{"do": ..., <parameter>: ...}`, validated by `engine.agro.parse`.
    program: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    #: The line the machine stands on, and since when: a fallow counts its
    #: days from here, and a setpoint is passed at once.
    cursor: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    step_since: Mapped[datetime] = created_column()

    #: The three storages of the yard its owner named (D-339). A storage
    #: burnt or taken apart forgets its role rather than taking the machine
    #: with it: the action that needed it stands until another is named.
    seeds_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("item.id", ondelete="SET NULL"), nullable=True
    )
    fertilizer_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("item.id", ondelete="SET NULL"), nullable=True
    )
    harvest_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("item.id", ondelete="SET NULL"), nullable=True
    )

    #: Until when the last action holds the machine: one machine is one pair
    #: of hands, and an action takes the minutes a hand's would (D-339).
    busy_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Why the machine stands, as a word the window and the journal translate
    #: (`engine.agro.TROUBLES`), or None while nothing it was asked to do is
    #: held back. Written when it changes, told to the owner then.
    trouble: Mapped[str | None] = mapped_column(nullable=True)
    #: The word last told to the owner's journal, and when: the same word is
    #: not told again within a Terran day (D-339 p. 11).
    told: Mapped[str | None] = mapped_column(nullable=True)
    told_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Up to what moment energy, lubricant and wear are counted.
    counted_at: Mapped[datetime] = created_column()
    created_at: Mapped[datetime] = created_column()


class FieldAutomatPlot(Base):
    """A plot given to a field automaton, in the order its owner listed them.

    A plot stands on one machine: two machines holding the moisture of one bed
    would water it twice and feed it twice.
    """

    __tablename__ = "field_automat_plot"
    __table_args__ = (Index("ix_field_automat_plot_automat", "automat_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    automat_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("field_automat.id", ondelete="CASCADE"), nullable=False
    )
    plot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plot.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    rank: Mapped[int] = mapped_column(nullable=False)

    created_at: Mapped[datetime] = created_column()
