# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Credit and the key rate (D-030, D-087, D-167).

A loan is a contract: the rate is fixed at issue and does not change afterwards,
whatever the bank decides later. Bank decisions live as a separate row so that
the rate's history can be shown whole: the algorithm is public, so its past is too.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class LoanState(StrEnum):
    OPEN = "open"
    REPAID = "repaid"


class Loan(Base):
    __tablename__ = "loan"
    __table_args__ = (Index("ix_loan_borrower", "identity_id", "state"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    #: Issued, in minor units.
    principal: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: How much is left to repay: principal plus accrued.
    outstanding: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: The borrower's rate, % per annum. Fixed at issue: a loan is a contract,
    #: not a subscription to the bank's decisions.
    rate: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    #: Through which city the loan was issued (D-175). Empty -- a direct loan
    #: from the capital at the worse rate: cheap credit is a privilege of citizenship.
    city_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("city.id"), nullable=True)
    #: The city's margin in the rate, % above the key rate. Stored separately:
    #: on an interest payment its share goes to the city treasury, the rest to the capital's
    #: reserve.
    margin: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)
    #: How much of the issued had to be printed: the emission-share sensor (D-087).
    printed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Interest: accrued and paid, cumulative (D-171). A payment covers it
    #: first, then the principal -- otherwise "system income" is unmeasurable.
    interest_accrued: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    interest_paid: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    state: Mapped[LoanState] = enum_column(
        LoanState, "loan_state", nullable=False, default=LoanState.OPEN
    )
    taken_at: Mapped[datetime] = created_column()
    #: Up to what moment interest is already accrued.
    accrued_at: Mapped[datetime] = created_column()
    #: When the loan was last paid. Overdue is counted from it (D-168): an
    #: unserviced debt is not "old" but "unpaid".
    serviced_at: Mapped[datetime] = created_column()
    repaid_at: Mapped[datetime | None] = mapped_column(nullable=True)


class RateDecision(Base):
    """A key-rate decision. The algorithm is public -- so the history is too."""

    __tablename__ = "rate_decision"
    __table_args__ = (Index("ix_rate_at", "decided_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    rate: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    #: What the sensors saw: inflation and the emission share of issue, percent.
    inflation: Mapped[float] = mapped_column(Numeric(8, 3), nullable=False, default=0)
    emission_share: Mapped[float] = mapped_column(Numeric(8, 3), nullable=False, default=0)
    #: In words: why it came out exactly so.
    why: Mapped[str] = mapped_column(nullable=False, default="")
    #: Until when the rate is returned to the algorithm in emergency (D-172).
    #: Not a punishment for the Council but a fuse: the price of a mistake is everybody's money.
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_column()


class DefectReport(Base):
    """A "defective print" report (D-173).

    By lore the printer sometimes prints people without intellect. A report
    neither kills nor bans -- it lowers trust, and trust cuts the credit limit.
    One per identity per identity: inflating with one account is impossible by construction.
    """

    __tablename__ = "defect_report"
    __table_args__ = (
        UniqueConstraint(
            "reporter_identity_id", "target_identity_id", name="uq_defect_report_pair"
        ),
        Index("ix_defect_report_target", "target_identity_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    reporter_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )
    target_identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    created_at: Mapped[datetime] = created_column()
