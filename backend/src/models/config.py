"""Runtime edits of balance constants and the journal of those edits.

D-065 demands three things: numbers are not hard-coded, they change without a
release, and **every change is recorded**. The third is no less important than
the first two: without a journal nobody will remember in a month why the ore
yield became different, and telemetry before the edit will stop meaning anything.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class ConstantOverride(Base):
    """An edit in force on top of `build/constants.json`.

    A key absent from the file cannot be here: an edit is a change of value,
    not the introduction of a new quantity. A new quantity is created in the vault.
    """

    __tablename__ = "constant_override"

    key: Mapped[str] = mapped_column(primary_key=True)
    #: The value can be a number, a string, `{min, max}` or a map -- the same
    #: shape as in the file, and it is checked by the registry on load.
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = created_column()


class ConstantChange(Base):
    """The immutable history of edits: who, when, what and why."""

    __tablename__ = "constant_change"

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(nullable=False)
    old_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    #: Who edited. The administrator's identity, not a game entity.
    author: Mapped[str] = mapped_column(nullable=False)
    reason: Mapped[str | None] = mapped_column(nullable=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    at: Mapped[datetime] = created_column()
