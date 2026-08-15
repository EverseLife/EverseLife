"""Worn gear: body slots (D-146).

One thing is worn in each slot. The slot is the constraint itself: without it
a player would wear three backpacks, and the carry limit would cease to exist.

What is worn **stays in the inventory** and weighs along with everything else:
an exoskeleton does not become weightless because it is put on. The slot
decides not "where it lies" but "whether it works": only what is worn raises the limit.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Equipped(Base):
    """What is worn on the body and in which slot."""

    __tablename__ = "equipped"
    __table_args__ = (
        UniqueConstraint("body_id", "slot", name="uq_equipped_body_slot"),
        Index("ix_equipped_item", "item_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    body_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("body.id"), nullable=False)
    #: Slot name from `build/recipes.json`: the engine does not invent them.
    slot: Mapped[str] = mapped_column(nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)

    created_at: Mapped[datetime] = created_column()
