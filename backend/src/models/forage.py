"""Foraging: one search per body on the empty land of a place (D-210).

The row is the whole state of the search. It exists while the body forages
here and is gone the moment they stop, take the find or walk away. What will
be found is decided at the start and kept in the row -- the roll seeded by the
row's id gives the same answer on any retry -- and it is **shown** only when
`ready_at` has passed. So a search needs no worker: nothing in the world
changes until the player decides about the find.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Forage(Base):
    """A running search, or a find waiting for a decision."""

    __tablename__ = "forage"
    __table_args__ = (
        #: One search per body: the same body cannot walk two plots at once.
        Index("uq_forage_body", "body_id", unique=True),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    body_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("body.id"), nullable=False)
    #: The plot being searched: leaving it abandons the search.
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)

    started_at: Mapped[datetime] = created_column()
    #: When the find shows itself. Before that the row is a search, after it an offer.
    ready_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: What turned up: the thing's key, its handful and its quality. Decided at
    #: the start, revealed by the deadline.
    found: Mapped[str] = mapped_column(nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    quality: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
