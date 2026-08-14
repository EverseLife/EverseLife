"""Суточные агрегаты мира (D-139, 60-meta/04).

**Считает движок, а не дашборд.** Причина не в удобстве: торговая сводка города
— игровой экран с той же агрегацией (D-124), и вторая копия формул в панелях
разошлась бы с первой. Здесь складываются суточные значения; панель их только
показывает.

Здесь же лежит то, ради чего всё это заводится: **память о вчера**. Проверка
вида «запас растёт две недели подряд» требует помнить предыдущий результат, а
живая выборка этого не умеет.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Index, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class DailyMetric(Base):
    """Одно измерение за сутки: ключ и значение."""

    __tablename__ = "daily_metric"
    __table_args__ = (
        UniqueConstraint("day", "key", name="uq_daily_metric_day_key"),
        Index("ix_daily_metric_key", "key", "day"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    #: Сутки, за которые снято измерение.
    day: Mapped[date] = mapped_column(nullable=False)
    #: Имя измерения: `money.total`, `stock.Руда`, `price.Уголь` и так далее.
    key: Mapped[str] = mapped_column(nullable=False)
    value: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)

    created_at: Mapped[datetime] = created_column()
