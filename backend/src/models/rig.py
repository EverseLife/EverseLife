"""Буровая установка: капитал вместо труда (D-115).

Станок, который добывает непрерывно и без игрока, — тот же переход от труда к
капиталу, что автоматический станок в крафте, только для добычи.

Это **не бесплатная руда, а предприятие с тремя обязательствами**: топливо,
опустошение бункера и обслуживание. Каждое требует людей, и потому богатому
человеку нужны углевоз, возчик и механик — капитал нанимает общество, а не
освобождает от него.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Rig(Base):
    """Установка, поставленная на жилу, и её бункер."""

    __tablename__ = "rig"
    __table_args__ = (
        Index("ix_rig_node", "node_id"),
        Index("ix_rig_vein", "vein_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    #: Сам станок: он же вещь с качеством и состоянием, которое надо чинить.
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    vein_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vein.id"), nullable=False)
    #: Кто поставил. Установка занимает узел и платит содержание (Э3).
    owner_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )

    #: Что уже добыто и ждёт вывоза. Полон бункер — установка стоит, и
    #: приезжать обязательно: без возчика предприятие не работает.
    hopper: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    #: До какого момента работа посчитана. Как и у пула энергии: машина живёт
    #: временем, а не кликом.
    counted_at: Mapped[datetime] = created_column()

    created_at: Mapped[datetime] = created_column()
