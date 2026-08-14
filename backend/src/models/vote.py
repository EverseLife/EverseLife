"""Голосование граждан: срок, ценз, кворум, порог (D-036, D-161).

Устав описывает процедуру пятью вопросами, и все они снимаются **в момент
открытия**: устав, изменённый посреди голосования, не переписывает правила уже
идущего. Иначе правитель, видя, что проигрывает, поднимал бы порог на ходу.
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
    """Предмет голосования. Машина одна, предметы разные."""

    #: Утверждение код-закона (`law_approval: citizens`).
    LAW = "law"
    #: Выборы правителя (`ruler_selection: elected_citizens`). Бюллетень
    #: называет кандидата, а не «да/нет» (D-162).
    ELECTION = "election"
    #: Отзыв правителя (`ruler_recall: by_citizens`): «да/нет» по человеку.
    RECALL = "recall"
    #: Правка самого устава (`charter_amendment`): свой порог, не `law_threshold`.
    CHARTER = "charter"
    #: Выборы в совет: побеждают столько кандидатов, сколько мест (D-164).
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
    #: Что решается: у закона — `{"law": id, "value": …}`.
    subject: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    opened_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )

    #: Условия сняты при открытии и дальше не меняются (D-161).
    threshold: Mapped[str] = mapped_column(nullable=False)
    quorum_share: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)
    #: Сколько человек имели право голоса на момент созыва: от них считается
    #: кворум. Иначе принятый в граждане завтра меняет вчерашний расклад.
    electorate: Mapped[int] = mapped_column(nullable=False, default=0)

    #: Кто голосует: `citizens` либо `council` (D-164). Снимается при созыве,
    #: как и всё прочее: роспуск совета посреди голосования не превращает его
    #: в общегородское.
    voters: Mapped[str] = mapped_column(nullable=False, default="citizens")

    state: Mapped[VoteState] = enum_column(
        VoteState, "vote_state", nullable=False, default=VoteState.OPEN
    )
    opened_at: Mapped[datetime] = created_column()
    closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Ballot(Base):
    """Голос. Открытый: поимённо видно, кто как проголосовал (D-161)."""

    __tablename__ = "ballot"
    __table_args__ = (
        UniqueConstraint("vote_id", "identity_id", name="uq_ballot_voter"),
        Index("ix_ballot_vote", "vote_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    vote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vote.id"), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )
    #: «Да/нет» у закона и отзыва. На выборах не значит ничего: там предмет —
    #: человек, и его называет `choice_identity_id`.
    yes: Mapped[bool] = mapped_column(nullable=False)
    #: За кого голос на выборах (D-162).
    choice_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )
    created_at: Mapped[datetime] = created_column()
