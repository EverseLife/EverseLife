"""Журнал событий — первичное хранилище.

Требование дизайна: суд, метрики и расследования опираются на полное событийное
логирование (01-tech-notes). Состояние мира — производное: любое изменение
сначала становится событием, и только потом отражается в таблицах состояния.

Восстановить это задним числом невозможно, поэтому пишется с первого дня всё —
даже то, что пока никто не читает.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from octoverse.db.base import Base


class EventKind(StrEnum):
    """Виды событий. Список растёт вместе с реестром действий (06-actions)."""

    # мир и служебное
    WORLD_BOOTSTRAPPED = "world.bootstrapped"
    CONSTANTS_CHANGED = "constants.changed"
    TICK_RAN = "tick.ran"

    # личность и тело
    IDENTITY_CREATED = "identity.created"
    BODY_PRINTED = "body.printed"
    BODY_DIED = "body.died"
    KNOWLEDGE_LEARNED = "knowledge.learned"

    # имущество
    ITEM_CREATED = "item.created"
    ITEM_MOVED = "item.moved"
    ITEM_CONSUMED = "item.consumed"
    ITEM_WORN = "item.worn"

    # деньги
    LEDGER_POSTED = "ledger.posted"


class Event(Base):
    __tablename__ = "event"
    __table_args__ = (
        Index("ix_event_kind_at", "kind", "at"),
        Index("ix_event_actor_at", "actor_identity_id", "at"),
        Index("ix_event_node_at", "node_id", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    kind: Mapped[str] = mapped_column(nullable=False)

    actor_identity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    #: Отпечаток набора констант, действовавшего в момент события. Без него
    #: разбор старого эпизода после правки баланса ничего не доказывает (D-065).
    constants_digest: Mapped[str | None] = mapped_column(nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Event {self.id} {self.kind}>"
