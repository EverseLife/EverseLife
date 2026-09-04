# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Mining session and device fee.

The roof is **not** here. It belongs to the working and lives on `Vein.roof`
(D-188), one number shared by everyone digging that vein (D-099); a session
kept a copy of it once, and the copy is what let two miners at one face
overwrite each other's sag. What is left here is the shift: whose body, which
vein, at what pace, how many swings and supports it took, and how it ended.

The roof stays **hidden** wherever it is kept: never shown to the player,
neither as a number nor as a derivative of one. Only a sign string with noise
goes out (D-143). If roof stability ever leaks into an API response, the
mechanic turns into arithmetic, and no noise will bring it back.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Integer, LargeBinary, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class SessionState(StrEnum):
    ACTIVE = "active"
    #: Left on their own -- what was mined is in the inventory.
    LEFT = "left"
    #: Collapse -- what was mined during the session is lost entirely.
    COLLAPSED = "collapsed"


class Pace(StrEnum):
    """Pace is the second lever of the same decision (D-091).

    Faster means more yield, more roof sag and more stamina spend. The stake
    doubles: you risk both a collapse and your reserve.
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

    #: The tool worked with. Wears per session, not per swing.
    tool_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    swings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timbers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[datetime] = created_column()
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)


class PowChallenge(Base):
    """The device fee: one Argon2id estimate per session (D-110, D-112).

    Power gives access but not advantage: computed faster -- started earlier.
    Yield meanwhile is determined by decisions at the face, not hardware. A
    thousand parallel sessions need a thousand times `pow.memory_per_session`
    of memory, and memory does not parallelise cheaply.
    """

    __tablename__ = "pow_challenge"
    __table_args__ = (Index("ix_pow_challenge_account", "account_id", "issued_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id"), nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    issued_at: Mapped[datetime] = created_column()
    solved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: The challenge is single-use: a solved one cannot be presented a second time.
    spent_on_session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
