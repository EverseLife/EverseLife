from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.settings import settings

#: Явные имена ограничений — иначе Alembic генерирует неповторяемые миграции,
#: а мир вечный и вайпов не бывает (D-007).
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: Словарь `dict` в аннотации поля означает JSONB — параметры законов,
#: полезная нагрузка событий и заданий (01-tech-notes).
#: Время **всегда** с зоной: сутки у каждой планеты свои (D-008), и наивная
#: метка времени в такой игре — гарантированная путаница.
TYPE_MAP = {
    dict[str, Any]: JSONB,
    dict: JSONB,
    datetime: DateTime(timezone=True),
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = TYPE_MAP


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


def created_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def enum_column(enum_type: type, name: str, **kw: Any) -> Any:
    """VARCHAR + CHECK вместо нативного типа Postgres.

    Нативный enum расширяется миграцией и блокирует таблицу; список состояний
    в игре меняется чаще, чем хотелось бы. В базе лежит **значение** элемента,
    а не его имя: `pending`, а не `PENDING` — чтобы запрос руками читался так
    же, как код.
    """
    return mapped_column(
        Enum(
            enum_type,
            native_enum=False,
            name=name,
            length=32,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        **kw,
    )


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings().database_url, pool_pre_ping=True, future=True)
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(engine(), expire_on_commit=False)
    return _sessionmaker


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


__all__ = [
    "Base",
    "created_column",
    "dispose",
    "engine",
    "enum_column",
    "session_factory",
    "uuid_pk",
]
