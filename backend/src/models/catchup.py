# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The one-off steps of the seed's catch-up that this world has taken (D-007).

Every other step of the catch-up reads the world and does what is still
missing. A one-off step cannot: what it writes cannot be told from a player's
choice the day after, so the world has to remember that it ran (`src.seed_once`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column


class CatchUpStep(Base):
    """One one-off step, done on this world -- or born done with it.

    A table of its own rather than an event: the journal is cut into months and
    will be cut back once a retention is decided (the code review, wave 4), and
    a step whose mark went with an old month would run again and undo what the
    players did since -- the defect this row exists to prevent.
    """

    __tablename__ = "catch_up_step"

    #: The step's key (`seed_once.ONCE`): a key of the schema, never a word.
    step: Mapped[str] = mapped_column(primary_key=True)
    #: What the step did, kept for whoever asks why later: the ports it drew, or
    #: that the world was born past it.
    result: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    at: Mapped[datetime] = created_column()
