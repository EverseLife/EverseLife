# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Court: case and sanction (D-095, D-117, D-166).

A case is a record "plaintiff accuses defendant in this city". A sanction is
what the engine enforces by verdict, and it is a separate row: timed sanctions
have an end, and lifting them must be a journal job's duty, not the judge's memory.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class CaseState(StrEnum):
    OPEN = "open"
    #: The verdict is delivered: the sanction is applied.
    JUDGED = "judged"
    #: Refused: an acquittal is also a verdict, and there are no hanging cases.
    DISMISSED = "dismissed"


class Case(Base):
    __tablename__ = "court_case"
    __table_args__ = (Index("ix_case_city_state", "city_id", "state"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("city.id"), nullable=False)
    plaintiff_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )
    defendant_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )
    #: The substance of the claim in words: the engine does not interpret it -- the judge's work.
    claim: Mapped[str] = mapped_column(nullable=False)
    #: The fee that went to the treasury on filing, in minor units.
    fee: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    state: Mapped[CaseState] = enum_column(
        CaseState, "case_state", nullable=False, default=CaseState.OPEN
    )
    judge_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )
    #: How it ended -- in the words of the verdict, for the case card.
    verdict: Mapped[str | None] = mapped_column(nullable=True)
    opened_at: Mapped[datetime] = created_column()
    judged_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Sanction(Base):
    """An applied sanction. A timed one is lifted by a journal job, not by memory."""

    __tablename__ = "sanction"
    __table_args__ = (
        Index("ix_sanction_target", "identity_id", "lifted_at"),
        Index("ix_sanction_city", "city_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("court_case.id"), nullable=True)
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("city.id"), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    #: A primitive from `laws.json`: the engine keeps no list of its own (D-094).
    kind: Mapped[str] = mapped_column(nullable=False)
    #: For a fine -- how much is awarded; for imprisonment -- the node held in.
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    node_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("node.id"), nullable=True)
    #: What could not be collected: debt to the city awaits its mechanic.
    debt: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    created_at: Mapped[datetime] = created_column()
    #: Until when it is in force. Empty -- indefinite.
    until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lifted_at: Mapped[datetime | None] = mapped_column(nullable=True)
