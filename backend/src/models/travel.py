"""Переход по ребру — длительное действие (06-actions, D-107).

Пока персонаж в пути, он **нигде**: тело остаётся привязанным к узлу, откуда
вышло, но присутственные действия ему закрыты. Так дорога получает цену не на
словах: за время перехода партию выкупят, а цену собьют.

Тело переезжает в новый узел **заданием журнала**, а не проверкой при чтении:
приход обязан случиться, даже если игрок закрыл вкладку сразу после выхода.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class TravelState(StrEnum):
    GOING = "going"
    ARRIVED = "arrived"
    #: Оборван, а не дошёл: тело погибло в пути. Отдельное состояние от
    #: «пришёл» обязательно — иначе разбор эпизода покажет приход туда, куда
    #: никто не приходил.
    CANCELLED = "cancelled"


class Travel(Base):
    __tablename__ = "travel"
    __table_args__ = (
        Index("ix_travel_body", "body_id", "state"),
        Index("ix_travel_due", "state", "arrives_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    body_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("body.id"), nullable=False)
    from_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    to_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    edge_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("edge.id"), nullable=False)

    state: Mapped[TravelState] = enum_column(
        TravelState, "travel_state", nullable=False, default=TravelState.GOING
    )
    #: Хвост автопути (D-045): id узлов, которые ещё предстоит пройти после
    #: этого отрезка. Пусто — обычный переход в соседний узел.
    plan: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime] = created_column()
    arrives_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    arrived_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Harness(Base):
    """Кто во что впряжён (D-157).

    Транспорт тяжелее человека и в руки не берётся никогда: он стоит в узле,
    как станок. Впряжённый едет за телом по всем переходам — это и есть
    единственный способ увезти больше, чем `inventory.carry_mass`.

    Ограничения стоят в базе, а не в проверках движка: тело тянет один
    транспорт, и один транспорт тянет одно тело. Обоз из двух возчиков — это
    конвой, и он приедет своей механикой.
    """

    __tablename__ = "harness"
    __table_args__ = (
        UniqueConstraint("body_id", name="uq_harness_body"),
        UniqueConstraint("item_id", name="uq_harness_item"),
        Index("ix_harness_body", "body_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    body_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("body.id"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("item.id"), nullable=False)
    created_at: Mapped[datetime] = created_column()
