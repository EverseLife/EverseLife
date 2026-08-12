"""Сессия добычи и плата устройства.

Ключевое поле здесь — `roof`. Оно **скрытое**: игроку не показывается никогда,
ни числом, ни производной от числа. Наружу уходит только строка признака с
шумом (D-143). Если устойчивость свода однажды утечёт в ответ API, механика
превратится в арифметику, и никакой шум её уже не вернёт.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Integer, LargeBinary, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from octoverse.db.base import Base, created_column, enum_column, uuid_pk


class SessionState(StrEnum):
    ACTIVE = "active"
    #: Ушёл сам — добытое в инвентаре.
    LEFT = "left"
    #: Обрушение — добытое за сессию потеряно целиком.
    COLLAPSED = "collapsed"


class Pace(StrEnum):
    """Темп — второй рычаг того же решения (D-091).

    Быстрее значит больше выхода, больше просадки свода и больше расхода
    выносливости. Ставка удваивается: рискуешь и обрушением, и запасом.
    """

    STEADY = "steady"
    FAST = "fast"


class MiningSession(Base):
    __tablename__ = "mining_session"
    __table_args__ = (
        Index("ix_mining_session_body", "body_id", "state"),
        Index("ix_mining_session_vein", "vein_id", "state"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    body_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("body.id"), nullable=False)
    vein_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vein.id"), nullable=False)
    state: Mapped[SessionState] = enum_column(
        SessionState, "mining_session_state", nullable=False, default=SessionState.ACTIVE
    )
    pace: Mapped[Pace] = enum_column(Pace, "mining_pace", nullable=False, default=Pace.STEADY)

    #: СКРЫТОЕ состояние. Не показывается игроку ни при каких условиях.
    roof: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)

    #: Инструмент, которым работают. Изнашивается за сессию, а не за удар.
    tool_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    swings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timbers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[datetime] = created_column()
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)


class PowChallenge(Base):
    """Плата устройства: одна оценка Argon2id на сессию (D-110, D-112).

    Мощность даёт доступ, но не преимущество: быстрее посчитал — раньше начал.
    Выход при этом определяют решения в забое, а не железо. Тысяча параллельных
    сеансов требует тысячу раз по `pow.memory_per_session` памяти, а память
    не параллелится дёшево.
    """

    __tablename__ = "pow_challenge"
    __table_args__ = (Index("ix_pow_challenge_account", "account_id", "issued_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id"), nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    issued_at: Mapped[datetime] = created_column()
    solved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: Задача одноразовая: решённую нельзя предъявить второй раз.
    spent_on_session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
