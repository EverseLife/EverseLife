"""Партия крафта — длительное действие, живущее без игрока.

Партия запускается присутственно (станок, инструмент и входы в узле), а идёт
офлайн: вход списан сразу, изделие появляется по сроку через журнал заданий
(06-actions, класс «длительное»).

Прогноз качества считается **до** списания материалов и хранится здесь же:
игрок увидел число до партии (D-092), и результат обязан быть выведен из того
же прогноза, а не посчитан заново по изменившимся с тех пор константам.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class BatchState(StrEnum):
    RUNNING = "running"
    DONE = "done"


class BatchKind(StrEnum):
    """Три длительные работы за одним верстаком (06-actions, крафт)."""

    #: Запустить партию: из материалов выходят изделия.
    MAKE = "make"
    #: Починить: состояние возвращается, потолок падает — вещь всё равно конечна.
    REPAIR = "repair"
    #: Переработать: вещь разбирается на часть материалов, разница — сток.
    RECYCLE = "recycle"


class CraftBatch(Base):
    """Одна запущенная партия."""

    __tablename__ = "craft_batch"
    __table_args__ = (
        Index("ix_craft_batch_body", "body_id", "state"),
        Index("ix_craft_batch_ready", "state", "ready_at"),
        CheckConstraint("units > 0", name="units_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    body_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("body.id"), nullable=False)
    #: Где стоит станок. Изделие появляется здесь же, а не в кармане мастера.
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)

    kind: Mapped[BatchKind] = enum_column(
        BatchKind, "craft_batch_kind", nullable=False, default=BatchKind.MAKE
    )
    #: Что делается — имя рецепта либо выхода операции из `build/recipes.json`.
    #: У починки и переработки это тип вещи, над которой работают.
    output: Mapped[str] = mapped_column(nullable=False)
    #: Вещь, которую чинят или разбирают. У обычной партии пусто.
    target_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: Сколько единиц выхода, во внутренних единицах (`units.AMOUNT_SCALE`).
    units: Mapped[int] = mapped_column(BigInteger, nullable=False)

    station_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    tool_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    #: Прогноз, показанный игроку до партии. Результат — он же плюс разброс.
    quality: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    #: Полуширина разброса: узкая при верных пропорциях, широкая при промахе.
    spread: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)

    #: Что списано на партию: имя входа → количество. Для журнала и разбора.
    spent: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    #: У котла: вид блюда (сочетание) и доля закрытых ролей (D-119, D-128).
    flavor: Mapped[str | None] = mapped_column(nullable=True)
    roles_filled: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)

    #: У монетного двора: проба, с которой чеканят (D-016). Она же решает,
    #: сколько металла вернёт переплавка этой партии.
    fineness: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)

    state: Mapped[BatchState] = enum_column(
        BatchState, "craft_batch_state", nullable=False, default=BatchState.RUNNING
    )

    started_at: Mapped[datetime] = created_column()
    ready_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
