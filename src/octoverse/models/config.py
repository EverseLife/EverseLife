"""Правки балансных констант в рантайме и журнал этих правок.

D-065 требует трёх вещей: числа не зашиты в код, меняются без выката версии и
**каждое изменение записано**. Третье не менее важно первых двух: без журнала
через месяц никто не вспомнит, почему выход руды стал другим, и телеметрия
до правки перестанет что-либо значить.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from octoverse.db.base import Base, created_column, uuid_pk


class ConstantOverride(Base):
    """Действующая правка поверх `build/constants.json`.

    Ключа, которого нет в файле, здесь быть не может: правка — это изменение
    значения, а не введение новой величины. Новая величина заводится в вольте.
    """

    __tablename__ = "constant_override"

    key: Mapped[str] = mapped_column(primary_key=True)
    #: Значением бывает число, строка, `{min, max}` или карта — форма та же,
    #: что в файле, и проверяется реестром при загрузке.
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = created_column()


class ConstantChange(Base):
    """Неизменяемая история правок: кто, когда, что и зачем."""

    __tablename__ = "constant_change"

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(nullable=False)
    old_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    #: Кто правил. Личность администратора, не игровая сущность.
    author: Mapped[str] = mapped_column(nullable=False)
    reason: Mapped[str | None] = mapped_column(nullable=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    at: Mapped[datetime] = created_column()
