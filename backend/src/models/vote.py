# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Citizens' vote: term, census, quorum, threshold (D-036, D-161).

The charter describes the procedure with five questions, and all of them are
captured **at opening**: a charter changed mid-poll does not rewrite the rules
of one already running. Otherwise a ruler who sees they are losing would raise
the threshold on the fly.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class VoteKind(StrEnum):
    """The subject of a vote. One machine, different subjects."""

    #: Approval of a code-law (`law_approval: citizens`).
    LAW = "law"
    #: Ruler election (`ruler_selection: elected_citizens`). The ballot names
    #: a candidate, not "yes/no" (D-162).
    ELECTION = "election"
    #: Ruler recall (`ruler_recall: by_citizens`): "yes/no" on a person.
    RECALL = "recall"
    #: Amendment of the charter itself (`charter_amendment`): its own threshold, not
    #: `law_threshold`.
    CHARTER = "charter"
    #: Council election: as many candidates win as there are seats (D-164).
    COUNCIL = "council"


class VoteState(StrEnum):
    OPEN = "open"
    PASSED = "passed"
    FAILED = "failed"


class Vote(Base):
    __tablename__ = "vote"
    __table_args__ = (Index("ix_vote_city_state", "city_id", "state"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("city.id"), nullable=False)
    kind: Mapped[VoteKind] = enum_column(VoteKind, "vote_kind", nullable=False)
    #: What is decided: for a law -- `{"law": id, "value": ...}`.
    subject: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    opened_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )

    #: Conditions captured at opening and not changed afterwards (D-161).
    threshold: Mapped[str] = mapped_column(nullable=False)
    quorum_share: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)
    #: How many people had a vote at convening: the quorum is counted from
    #: them. Otherwise somebody admitted tomorrow changes yesterday's tally.
    electorate: Mapped[int] = mapped_column(nullable=False, default=0)

    #: Who votes: `citizens` or `council` (D-164). Captured at convening like
    #: everything else: dissolving the council mid-poll does not turn it into a
    #: city-wide one.
    voters: Mapped[str] = mapped_column(nullable=False, default="citizens")

    state: Mapped[VoteState] = enum_column(
        VoteState, "vote_state", nullable=False, default=VoteState.OPEN
    )
    opened_at: Mapped[datetime] = created_column()
    closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Ballot(Base):
    """A vote. Open: it is visible by name who voted how (D-161)."""

    __tablename__ = "ballot"
    __table_args__ = (
        UniqueConstraint("vote_id", "identity_id", name="uq_ballot_voter"),
        Index("ix_ballot_vote", "vote_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    vote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vote.id"), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    #: "Yes/no" for a law and a recall. Means nothing in an election: there
    #: the subject is a person, named by `choice_identity_id`.
    yes: Mapped[bool] = mapped_column(nullable=False)
    #: Whom the vote is for in an election (D-162).
    choice_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )
    created_at: Mapped[datetime] = created_column()
