# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

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

#: Explicit constraint names -- otherwise Alembic generates non-reproducible
#: migrations, and the world is eternal with no wipes (D-007).
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: A `dict` in a field annotation means JSONB -- law parameters, event and job
#: payloads (01-tech-notes).
#: Time is **always** zoned: every planet has its own day (D-008), and a naive
#: timestamp in such a game is guaranteed confusion.
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
    """VARCHAR + CHECK instead of the native Postgres type.

    A native enum is extended by a migration and locks the table; the list of
    states in the game changes more often than one would like. The database
    holds the member's **value**, not its name: `pending`, not `PENDING` -- so
    that a hand-written query reads the same as code.
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
        conf = settings()
        _engine = create_async_engine(
            conf.database_url,
            pool_pre_ping=True,
            future=True,
            #: Every session command opens a transaction of its own
            #: (`api/session.py`), so the pool is the count of players the
            #: server serves **at the same instant**. The library's default --
            #: five plus ten -- is a queue at a hundred connected. Under
            #: `--workers N` each process holds a pool of its own, and their
            #: sum must fit the database's `max_connections`.
            pool_size=conf.db_pool_size,
            max_overflow=conf.db_max_overflow,
        )
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
