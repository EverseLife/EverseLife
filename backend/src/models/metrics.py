"""The world's daily aggregates (D-139, 60-meta/04).

**The engine computes, not the dashboard.** The reason is not convenience:
the city's trade summary is a game screen with the same aggregation (D-124),
and a second copy of the formulas in panels would diverge from the first.
Daily values are stored here; the panel only shows them.

Here also lies what all this is created for: **the memory of yesterday**. A
check like "stock grows two weeks in a row" needs the previous result
remembered, and a live query cannot do that.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Index, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class DailyMetric(Base):
    """One measurement per day: key and value."""

    __tablename__ = "daily_metric"
    __table_args__ = (
        UniqueConstraint("day", "key", name="uq_daily_metric_day_key"),
        Index("ix_daily_metric_key", "key", "day"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    #: The day the measurement was taken for.
    day: Mapped[date] = mapped_column(nullable=False)
    #: The measurement name: `money.total`, `stock.<ore>`, `price.<coal>` and so on.
    key: Mapped[str] = mapped_column(nullable=False)
    value: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)

    created_at: Mapped[datetime] = created_column()
