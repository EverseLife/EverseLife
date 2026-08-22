# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Hashable
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, MetaData, event, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

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


#: Where one command's answers lie on the session.
_MEMO = "everselife.remembered"


async def remember[T](
    session: AsyncSession, key: Hashable, produce: Callable[[], Awaitable[T]]
) -> T:
    """Answer a repeated question once per command.

    A session command is one transaction (`api/session.py`), and inside it the
    engine asks for the same rows again and again: one `look` wanted the node's
    yard twenty-three times and the same building area three. That is the price
    of reading through many small helpers, each saying one thing -- and the
    helpers are worth keeping. The round trip behind every repeat is not: the
    database spends microseconds on these queries, the wait is the hundred trips.

    The memory lives on the session and dies with it: nothing is kept between
    players or between commands. It is emptied on **any** write, so a helper
    that answered before a flush cannot answer the same after it -- and since
    SQLAlchemy flushes pending changes before the next query by itself, a
    command that changes the world loses the memory before it can mislead.
    """
    #: A remembered answer is given **without** a query, and a query is what
    #: would have flushed the changes waiting on the session. So they are
    #: flushed here -- which throws the memory away and makes the answer be
    #: read anew. Without this, code that added a thing and then asked what
    #: lies in the container would not see what it had just put there.
    if session.new or session.dirty or session.deleted:
        await session.flush()

    kept: dict[Hashable, Any] = session.info.setdefault(_MEMO, {})
    if key in kept:
        return kept[key]
    value = await produce()
    #: Asked for again rather than written into `kept`: the answer may have
    #: been produced by a write -- the node's yard is created on first ask --
    #: and that write threw the whole memory away in between.
    session.info.setdefault(_MEMO, {})[key] = value
    return value


@event.listens_for(Session, "after_flush")
def _forget_on_write(session: Session, context: Any) -> None:
    """A write invalidates everything remembered: what stands in the yard, how
    much is built, where the pocket is -- any of it may be what just changed."""
    session.info.pop(_MEMO, None)


@event.listens_for(Session, "do_orm_execute")
def _forget_on_bulk_write(state: Any) -> None:
    """The same for a statement that writes without the objects.

    An `UPDATE`/`DELETE` handed to the session goes straight to the database
    and never passes through a flush, so `after_flush` above does not see it.
    Nothing in the engine currently empties a container that way -- the bulk
    statements it does write are access lists, chat and foraging, none of them
    remembered -- but this is not a thing to leave standing on what the engine
    happens to do today.
    """
    if state.is_update or state.is_delete:
        state.session.info.pop(_MEMO, None)


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
    "remember",
    "session_factory",
    "uuid_pk",
]
