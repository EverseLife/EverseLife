"""Живое общение в локации (D-043, D-050).

Разговор в комнате, а не переписка: слышат находящиеся рядом, вышел — вышел из
разговора. **Сервер истории не ведёт** (D-070, D-081): сказанное вслух не
хранится, и никакой судебный запрос его не поднимет, потому что поднимать
нечего.

Таблица ниже — не история, а **буфер доставки**: клиенты опрашивают чат, и до
следующего опроса реплика должна где-то лежать. Буфер подметается тиком мира
по сроку `runtime.CHAT_BUFFER` — величине исполнения, а не баланса.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

#: `text` взят с псевдонимом: внутри `ChatMessage` это имя — колонка реплики.
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy import text as sql
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class Utterance(StrEnum):
    """Три типа сообщений, и тип обязателен (D-050).

    Без «действия» отыгрыш неотличим от реплик; без «вне игры» метагейм
    смешивается с игровым, и на суде не понять, что сказал персонаж, а что
    человек.
    """

    #: Прямая речь персонажа.
    SPEECH = "speech"
    #: Описание от третьего лица.
    ACTION = "action"
    #: Разговор игроков, а не персонажей. Явно отделён и фильтруется.
    OOC = "ooc"


class ChatGroup(Base):
    """Кружок внутри локации: группы видны, их содержание — нет (D-043)."""

    __tablename__ = "chat_group"
    __table_args__ = (Index("ix_chat_group_node", "node_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    #: Группу можно назвать, а можно оставить безымянной.
    name: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = created_column()


class ChatMember(Base):
    """Кто в каком кружке. Подошедшего к кружку человека видно."""

    __tablename__ = "chat_member"
    __table_args__ = (
        #: Личность стоит в одном кружке: в двух разговорах разом не шепчутся.
        UniqueConstraint("identity_id", name="uq_chat_member_identity"),
        Index("ix_chat_member_group", "group_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_group.id", ondelete="CASCADE"), nullable=False
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    joined_at: Mapped[datetime] = created_column()


class ChatMessage(Base):
    """Одна реплика в буфере доставки."""

    __tablename__ = "chat_message"
    __table_args__ = (Index("ix_chat_message_node_at", "node_id", "at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    #: Пусто — общий разговор локации, слышный всем в ней.
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_group.id", ondelete="CASCADE"), nullable=True
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)

    kind: Mapped[Utterance] = enum_column(Utterance, "utterance", nullable=False)
    #: Вполголоса: меньше утечек, но и своих слышно хуже (D-043).
    quiet: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    text: Mapped[str] = mapped_column(nullable=False)

    #: Реплика долетела до чужих ушей: разыгрывается один раз при отправке.
    leaked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: `clock_timestamp()`, а не `now()`: у Постгреса `now()` заморожен на всю
    #: транзакцию, а репликам нужен настенный порядок — по нему режется «слышно
    #: с момента прихода».
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql("clock_timestamp()"), nullable=False
    )
