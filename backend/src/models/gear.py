"""Надетое снаряжение: слоты тела (D-146).

В каждый слот надевается одна вещь. Слот — это и есть ограничение: без него
игрок надел бы три рюкзака, и предел носимого перестал бы существовать.

Надетое **остаётся в инвентаре** и весит вместе со всем прочим: экзоскелет не
становится невесомым оттого, что его надели. Слот решает не «где лежит», а
«работает ли»: только надетое поднимает предел.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Equipped(Base):
    """Что надето на теле и в каком слоте."""

    __tablename__ = "equipped"
    __table_args__ = (
        UniqueConstraint("body_id", "slot", name="uq_equipped_body_slot"),
        Index("ix_equipped_item", "item_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    body_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("body.id"), nullable=False)
    #: Имя слота из `build/recipes.json`: движок их не выдумывает.
    slot: Mapped[str] = mapped_column(nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)

    created_at: Mapped[datetime] = created_column()
