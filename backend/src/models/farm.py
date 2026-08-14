"""Делянка — единица агрономии (D-118).

Одна культура, один срок, одно состояние, один обход. Земля меряется метрами:
хозяин сам режет участок на делянки и сам ищет баланс — дробить дорого (обход
на каждую), укрупнять рискованно (болезнь и монокультура).

**Плодородие и история культур принадлежат земле, а не разметке** (И5):
при делении обе части наследуют их как есть, при слиянии плодородие берётся
взвешенным, история — самой тяжёлой. Без этого передел границ был бы
бесплатным сбросом истощения.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class PlotState(StrEnum):
    #: Незасеяна и не вспахана. Только такую можно перекраивать.
    IDLE = "idle"
    #: Пашется: длительное действие, идёт заданием журнала.
    PLOWING = "plowing"
    #: Вспахана, готова к посеву.
    PLOWED = "plowed"
    #: Растёт. Зрелость выводится из времени, отдельного состояния ей не нужно.
    SOWN = "sown"


class Plot(Base):
    __tablename__ = "plot"
    __table_args__ = (
        Index("ix_plot_node", "node_id"),
        Index("ix_plot_owner", "owner_identity_id"),
        CheckConstraint("area_m2 > 0", name="area_positive"),
        CheckConstraint("fertility >= 0 AND fertility <= 100", name="fertility_in_scale"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    #: Кто разметил. Титул на землю и аренда приезжают с городами (Э3).
    owner_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )

    name: Mapped[str] = mapped_column(nullable=False)
    area_m2: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    state: Mapped[PlotState] = enum_column(
        PlotState, "plot_state", nullable=False, default=PlotState.IDLE
    )

    #: Плодородие земли под этой разметкой, 0…100.
    fertility: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)

    #: История: что росло последним и сколько циклов подряд. По ней считается
    #: истощение монокультуры (`farm.soil_depletion`).
    last_culture: Mapped[str | None] = mapped_column(nullable=True)
    same_culture_cycles: Mapped[int] = mapped_column(nullable=False, default=0)

    #: Что растёт сейчас — id культуры из `build/plants.json`.
    culture_id: Mapped[str | None] = mapped_column(nullable=True)
    #: Чей сорт посеян и с какой силой было семя: урожай считается по ним,
    #: а не по числам культуры (D-057).
    variety_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    seed_vigor: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    sown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Зачтённые сутки ухода этого цикла и когда ухаживали в последний раз.
    care_credits: Mapped[int] = mapped_column(nullable=False, default=0)
    cared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: С какого момента земля стоит под паром: восстановление начисляется по
    #: факту времени при следующем действии — тик ей не нужен.
    idle_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = created_column()
