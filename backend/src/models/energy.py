# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""City energy: the shared pool (D-071, D-082).

Inside a city energy is not routed anywhere: everything standing on its
territory feeds from **one pool**. There are and will be no separate
connections, wires or lines between city nodes -- distribution is simplified,
not scarce.

**The pool belongs to the city**, and the city as an institution does not
exist yet: it arrives with E3 together with the charter and treasury. Until
then the pool lives on the city's delegate node -- the very one whose children
in the display hierarchy are the city's built-up area (`Node.parent_id`). City
territory = its children, and that is exactly what a city is on the map today.
When `City` appears the pool moves to it without a change of meaning: rebind the key.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class EnergyPool(Base):
    """The city's charge and the moment up to which it is computed."""

    __tablename__ = "energy_pool"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: The city's delegate node. One pool per city.
    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node.id"), nullable=False, unique=True
    )

    #: How much energy is in the pool now.
    stored: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False, default=0)
    #: Up to what moment generation is already credited. Production runs by
    #: time, not by click: the tick brings the pool up to "now" and moves this stamp.
    counted_at: Mapped[datetime] = created_column()

    #: Release tariff, TC per 100 energy. Edited by the city charter from E3
    #: (D-085); until then the vault default lies here, and players see it too.

    tariff: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    created_at: Mapped[datetime] = created_column()
