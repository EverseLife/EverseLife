# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A snapshot of the public map (D-319 п. 7).

`/public/map` without a token answers the whole internet, and it must not
show what happens at the far end of the world **now**: that would be unfair
to whoever is there. So the daily tick writes the public part of every
planet's surface -- the nodes, the ways, the surfaces, the groups -- as one
record, and the route serves the newest record that is at least
`map.public_delay_days` old. A snapshot, not a filter: no field on a node
says "public since", and the records older than the one served are pruned by
the same tick.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Index
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class MapSnapshot(Base):
    __tablename__ = "map_snapshot"
    __table_args__ = (Index("ix_map_snapshot_taken", "taken_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    #: The moment the tick took it: the route compares this with the delay.
    taken_at: Mapped[datetime] = created_column()
    #: `{"nodes": [...], "edges": [...]}` exactly as the route sends them
    #: (`sight.node_row`, `sight.edge_row`).
    data: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
