"""Журнал заданий — основа тика мира.

Мир живёт без игроков: партии, караваны, рост урожая, суточное содержание,
счётчики, порча. Всё это отложенные события, и каждое обязано выполниться
**ровно один раз**, даже если процесс перезапустили посреди тика
(01-tech-notes, паттерн 1).

Отсюда конструкция: не «крон дёргает функцию», а таблица заданий с состоянием,
выборка `FOR UPDATE SKIP LOCKED` и завершение задания **в одной транзакции с
его эффектами**. Ключ `dedup_key` уникален — повторная постановка того же
задания не создаёт второго.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from octoverse.db.base import Base, created_column, enum_column, uuid_pk


class JobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobKind(StrEnum):
    """Виды отложенной работы. Обработчик регистрируется в `engine.tick`."""

    WORLD_TICK = "world.tick"
    DAILY_TICK = "world.daily"
    CRAFT_BATCH = "craft.batch"
    TRAVEL_LEG = "travel.leg"
    MARKET_ORDER_EXPIRY = "market.order_expiry"
    SPOILAGE = "item.spoilage"


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (
        #: Рабочая выборка воркера: только ожидающие, по времени срабатывания.
        Index(
            "ix_job_due",
            "run_at",
            postgresql_where=text("state = 'pending'"),
        ),
        Index("ix_job_state_kind", "state", "kind"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(nullable=False)
    state: Mapped[JobState] = enum_column(
        JobState, "job_state", nullable=False, default=JobState.PENDING, index=False
    )

    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    #: Идемпотентность. Задание «списать содержание за 12-е сутки с дома X»
    #: ставится сколько угодно раз и существует в одном экземпляре.
    dedup_key: Mapped[str | None] = mapped_column(unique=True, nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(nullable=True)

    #: Кто взял задание и когда — для разбора зависших воркеров.
    locked_by: Mapped[str | None] = mapped_column(nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = created_column()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Событие, породившее задание, — цепочка причин для расследования.
    cause_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    body_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Job {self.kind} {self.state} run_at={self.run_at}>"
