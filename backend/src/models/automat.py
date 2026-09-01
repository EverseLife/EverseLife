# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The automat: a station that works without the player (D-253).

The second production order, after the rig (D-115) and beside the field
automaton (D-120): a machine standing in a building executes the recipe its
owner programmed, slower than a hand and never above its quality ceiling,
for as long as the building's vessels hold lubricant, the city pool holds
energy and the yard holds inputs. Any of the three runs out -- it stands.

This is **an enterprise, not free goods**: lubricant is hauled like the
rig's coal, energy is billed to the owner at the city tariff, and the
machine wears whether it works or stands. Capital hires society.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Automat(Base):
    """An automat standing in a node, and the recipe it is set to."""

    __tablename__ = "automat"
    __table_args__ = (Index("ix_automat_node", "node_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    #: The machine itself: also a thing with quality and condition that needs repairing.
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    #: Who programmed it. The energy bill goes here (D-135: whoever burns pays).
    owner_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )

    #: The programmed output (a D-251 goods key), or NULL -- the machine idles.
    #: One recipe per machine: chains between machines are the node editor's
    #: business (D-253, wave 5), not a second recipe slot.
    recipe_key: Mapped[str | None] = mapped_column(nullable=True)

    #: Units of work done but not yet paid out: a piece crosses tick
    #: boundaries, and a liquid waits for room in a vessel. Inputs are
    #: consumed at payout, so the backlog is time, not matter.
    backlog: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)

    #: Up to what moment work is computed. As with the rig: the machine lives
    #: by time, not by click.
    counted_at: Mapped[datetime] = created_column()

    created_at: Mapped[datetime] = created_column()
