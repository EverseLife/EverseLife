# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Public works: the state order board (D-248).

An order is money already set aside: posting one escrows its payout from the
works fund, so the board can never promise what the fund does not hold. The
order is claimless -- whoever completes the verifiable work first collects.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class WorkOrderKind(StrEnum):
    #: Restore a sagged edge surface (auto-posted by the engine, D-152/D-158).
    ROAD_MEND = "road_mend"
    #: City orders (D-248): repair, construction, fuel delivery. Posted by a
    #: city with the TREASURY power; the city co-finances the tariff.
    BUILDING_REPAIR = "building_repair"
    BUILDING_BUILD = "building_build"
    FUEL_DELIVERY = "fuel_delivery"


class WorkOrderState(StrEnum):
    OPEN = "open"
    DONE = "done"
    #: The target changed under the order (the edge decayed a tier, the
    #: building fell): the escrow went back to the fund.
    CANCELLED = "cancelled"


class WorkOrder(Base):
    __tablename__ = "work_order"
    __table_args__ = (
        Index("ix_work_order_state", "kind", "state"),
        Index("ix_work_order_edge", "edge_id"),
        #: One open order per object, held by the database rather than by the
        #: single daily poster happening to be alone today -- or by two city
        #: commands happening not to race.
        Index(
            "uq_work_order_open_edge",
            "kind",
            "edge_id",
            unique=True,
            postgresql_where=text("state = 'open'"),
        ),
        Index(
            "uq_work_order_open_node",
            "kind",
            "node_id",
            unique=True,
            postgresql_where=text("state = 'open'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[WorkOrderKind] = enum_column(WorkOrderKind, "work_order_kind", nullable=False)
    state: Mapped[WorkOrderState] = enum_column(
        WorkOrderState, "work_order_state", nullable=False, default=WorkOrderState.OPEN
    )
    #: The target. Road orders point at an edge; city orders at a node --
    #: which one is which the kind says.
    edge_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("edge.id"), nullable=True)
    node_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("node.id"), nullable=True)
    #: The ordering city. Empty for auto orders: the world itself is the customer.
    city_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("city.id"), nullable=True)
    #: Kind-specific details: the surface tier the road order was posted
    #: against, the goods and amount of a delivery.
    payload: Mapped[dict] = mapped_column(nullable=False, default=dict)
    #: The full payout, minor units. Escrowed at posting: the board never
    #: promises what is not already set aside.
    tariff: Mapped[int] = mapped_column(BigInteger, nullable=False)
    done_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("identity.id"), nullable=True)
    posted_at: Mapped[datetime] = created_column()
    done_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(nullable=True)
