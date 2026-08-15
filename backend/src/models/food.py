"""The meal journal (D-105).

Dietary variety is counted **by what was eaten, not by stocks**: stuffing the
bag with three kinds of food is not enough, they have to be eaten. Hence the
table: the last `food.variety_window` meals, and if among them there are at
least `food.variety_min_kinds` different kinds, the bonus works.

The journal belongs to the identity: taste is a person's memory, not the stomach's.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Meal(Base):
    __tablename__ = "meal"
    __table_args__ = (Index("ix_meal_identity_at", "identity_id", "at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    #: The kind of what was eaten: for dry food the item type, for a dish the combination (D-128).
    flavor: Mapped[str] = mapped_column(nullable=False)
    at: Mapped[datetime] = created_column()
