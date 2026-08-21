# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A transit along an edge -- a long-running action (06-actions, D-107).

While a character is on the way, they are **nowhere**: the body stays bound to
the node it left, but in-person actions are closed to it. So the road gets a
price not in words: during the transit the lot gets bought and the price beaten down.

The body moves to the new node **by a journal job**, not by a check on read:
the arrival must happen even if the player closed the tab right after leaving.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class TravelState(StrEnum):
    GOING = "going"
    ARRIVED = "arrived"
    #: Cut off rather than arrived: the body died en route. A separate state
    #: from "arrived" is mandatory -- otherwise examining the episode would
    #: show an arrival where nobody arrived.
    CANCELLED = "cancelled"


class Travel(Base):
    __tablename__ = "travel"
    __table_args__ = (
        Index("ix_travel_body", "body_id", "state"),
        Index("ix_travel_due", "state", "arrives_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    body_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("body.id"), nullable=False)
    from_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    to_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    #: Which edge the leg went by. Empty means the edge is gone: a ship
    #: undocked and took its gangway with it (D-201). The edge is removed
    #: rather than flagged, so the journal of past transits must survive its
    #: disappearance -- from, to and the times are the record, the edge is a
    #: pointer. A leg **under way** never loses it: `travel.disconnect` refuses
    #: to remove an edge somebody is walking.
    edge_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("edge.id", ondelete="SET NULL"), nullable=True
    )

    state: Mapped[TravelState] = enum_column(
        TravelState, "travel_state", nullable=False, default=TravelState.GOING
    )
    #: The autopath tail (D-045): ids of nodes still to be walked after this
    #: leg. Empty -- an ordinary transit to an adjacent node.
    plan: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime] = created_column()
    arrives_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    arrived_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Harness(Base):
    """Who is harnessed to what (D-157).

    A vehicle is heavier than a person and is never taken in hand: it stands in
    the node, like a machine. A harnessed one follows the body along all
    transits -- that is the only way to carry more than `inventory.carry_mass`.

    The constraints are in the database, not in engine checks: a body pulls one
    vehicle, and one vehicle is pulled by one body. A convoy of two carters is
    a caravan, and it will arrive with its own mechanic.
    """


    __tablename__ = "harness"
    __table_args__ = (
        UniqueConstraint("body_id", name="uq_harness_body"),
        UniqueConstraint("item_id", name="uq_harness_item"),
        Index("ix_harness_body", "body_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    body_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("body.id"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("item.id"), nullable=False)
    created_at: Mapped[datetime] = created_column()
