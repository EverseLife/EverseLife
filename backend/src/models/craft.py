"""A craft batch -- a long-running action living without the player.

A batch is started in person (machine, tool and inputs in the node) and runs
offline: the input is written off at once, the product appears on schedule via
the job journal (06-actions, class "long-running").

The quality forecast is computed **before** materials are written off and
stored right here: the player saw the number before the batch (D-092), and the
result must be derived from that same forecast, not recomputed from constants
that changed since.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class BatchState(StrEnum):
    RUNNING = "running"
    DONE = "done"


class BatchKind(StrEnum):
    """Three long-running works at one workbench (06-actions, craft)."""

    #: Start a batch: products come out of materials.
    MAKE = "make"
    #: Repair: condition comes back, the ceiling drops -- the thing is finite anyway.
    REPAIR = "repair"
    #: Recycle: the thing is taken apart for part of the materials, the difference is a sink.
    RECYCLE = "recycle"


class CraftBatch(Base):
    """One started batch."""

    __tablename__ = "craft_batch"
    __table_args__ = (
        Index("ix_craft_batch_body", "body_id", "state"),
        Index("ix_craft_batch_ready", "state", "ready_at"),
        CheckConstraint("units > 0", name="units_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    body_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("body.id"), nullable=False)
    #: Where the machine stands. The product appears right here, not in the master's pocket.
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)

    kind: Mapped[BatchKind] = enum_column(
        BatchKind, "craft_batch_kind", nullable=False, default=BatchKind.MAKE
    )
    #: What is being made -- the recipe name or the operation output from
    #: `build/recipes.json`. For repair and recycling it is the type of the thing worked on.
    output: Mapped[str] = mapped_column(nullable=False)
    #: The thing being repaired or taken apart. Empty for an ordinary batch.
    target_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: How many units of output, in internal units (`units.AMOUNT_SCALE`).
    units: Mapped[int] = mapped_column(BigInteger, nullable=False)

    station_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    tool_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    #: The forecast shown to the player before the batch. The result is it plus spread.
    quality: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    #: Spread half-width: narrow with correct proportions, wide on a miss.
    spread: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)

    #: What was written off for the batch: input name -> amount. For the journal and examination.
    spent: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    #: For a pot: dish kind (combination) and share of filled roles (D-119, D-128).
    flavor: Mapped[str | None] = mapped_column(nullable=True)
    roles_filled: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)

    #: For the mint: the fineness minted at (D-016). It also decides how much
    #: metal melting this batch returns.

    fineness: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)

    state: Mapped[BatchState] = enum_column(
        BatchState, "craft_batch_state", nullable=False, default=BatchState.RUNNING
    )

    started_at: Mapped[datetime] = created_column()
    ready_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
