# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Drilling rig: capital instead of labour (D-115).

A machine that mines continuously and without the player is the same
transition from labour to capital as the automatic machine in craft, only for mining.

This is **not free ore but an enterprise with three obligations**: fuel,
emptying the hopper and maintenance. Each requires people, and so a rich
person needs a coal hauler, a carter and a mechanic -- capital hires society
rather than freeing one from it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Rig(Base):
    """A rig placed on a vein, and its hopper."""

    __tablename__ = "rig"
    __table_args__ = (
        Index("ix_rig_node", "node_id"),
        Index("ix_rig_vein", "vein_id"),
        CheckConstraint(
            "hopper_remainder >= 0 AND hopper_remainder < 0.001",
            name="hopper_remainder_under_a_thousandth",
        ),
        CheckConstraint(
            "fuel_remainder >= 0 AND fuel_remainder < 0.001",
            name="fuel_remainder_under_a_thousandth",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    #: The machine itself: also a thing with quality and condition that needs repairing.
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    vein_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vein.id"), nullable=False)
    #: Who placed it. The rig occupies a node and pays maintenance (E3).
    owner_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )

    #: What is already mined and awaits hauling. Hopper full -- the rig stands,
    #: and coming is mandatory: without a carter the enterprise does not work.
    hopper: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    #: Ore raised that the hopper could not be credited with. It keeps
    #: thousandths, and a short pass raises less than one -- and the vein was
    #: being emptied for it all the same, so the ore left the world without
    #: reaching anybody. Kept here it is credited on the next pass. It cannot
    #: ride on `counted_at`: that stamp measures the mining, the fuel and the
    #: wear, and holding it back would raise the same ore twice.
    #: Always `0 <= hopper_remainder < 0.001`, and a check says so.
    hopper_remainder: Mapped[float] = mapped_column(
        Numeric(9, 9), nullable=False, default=0, server_default="0"
    )
    #: Coal owed for ore already raised. Fuel is written off in thousandths
    #: too, and the coal a thousandth of ore costs is thinner than that again
    #: -- so a rig settled often raised ore and burned nothing. Kept here it
    #: is burned once it comes to a thousandth.
    #: Always `0 <= fuel_remainder < 0.001`, and a check says so.
    fuel_remainder: Mapped[float] = mapped_column(
        Numeric(9, 9), nullable=False, default=0, server_default="0"
    )
    #: Up to what moment work is computed. As with the energy pool: the machine
    #: lives by time, not by click.

    counted_at: Mapped[datetime] = created_column()

    created_at: Mapped[datetime] = created_column()
