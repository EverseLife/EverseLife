"""Журнал приёмов пищи (D-105).

Разнообразие рациона считается **по съеденному, а не по запасам**: набить сумку
тремя видами еды недостаточно, надо их есть. Отсюда таблица: последние
`food.variety_window` приёмов, и если среди них не меньше
`food.variety_min_kinds` разных видов — работает надбавка.

Журнал принадлежит личности: вкус — память человека, а не желудка.
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
    #: Вид съеденного: у сухого — тип предмета, у блюда — сочетание (D-128).
    flavor: Mapped[str] = mapped_column(nullable=False)
    at: Mapped[datetime] = created_column()
