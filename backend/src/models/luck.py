# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The memory of a chance: how long this identity has been out of luck (D-213).

A fair coin is not the same as a fair deal. At a 22% chance of a find, twelve
empty runs in a row happen once in eighteen evenings -- rare enough to be a
surprise, common enough to be somebody's whole evening. Competitive games solve
it the same way and have for years: the chance grows with every failure and
resets on success, with the growth chosen so that the **mean stays exactly what
was announced**.

That growth needs one number remembered per identity per matter: how many times
in a row it has not worked. A deck needs a little more -- what is left in it --
and both live in this one row, because they are the same thing: a chance that
remembers what it has already given out.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Luck(Base):
    """One identity's memory of one matter: "разведка нашла", "обвал убил"."""

    __tablename__ = "luck"
    __table_args__ = (
        #: One row per identity per matter: the counter is the row.
        Index("uq_luck_matter", "identity_id", "matter", unique=True),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    #: What the luck is about, by the engine's own key: `explore.find`,
    #: `mine.wound`, `forage.what`. ASCII by design -- it is an id, not a word
    #: of the interface.
    matter: Mapped[str] = mapped_column(nullable=False)

    #: How many times in a row it has not worked. Reset by success; the
    #: effective chance is this plus one, times the constant derived from the
    #: announced chance.
    misses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: What the deck still holds, for a choice of many: thing -> how many draws
    #: are left in it. Empty for a plain yes-or-no chance.
    deck: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    updated_at: Mapped[datetime] = created_column()
