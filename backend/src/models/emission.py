# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Emission by signatures (D-270): the capital prints money into its own treasury.

A proposal names the sum; whoever holds the `emission` right signs it; when
the signatures reach the share the vault sets of all who hold the right, the
sum is printed. One live proposal per city at a time, and a proposal lives
`emission.proposal_hours` -- an unsigned sum does not hang over the treasury
for ever.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class EmissionState(StrEnum):
    #: Collecting signatures.
    OPEN = "open"
    #: The signatures came together and the sum was printed.
    PRINTED = "printed"
    #: The term ran out before the signatures did.
    EXPIRED = "expired"


class EmissionProposal(Base):
    """One proposal to print: the sum, who asked, and how long it stands."""

    __tablename__ = "emission_proposal"
    __table_args__ = (
        Index("ix_emission_city", "city_id"),
        #: One live proposal per city: two sums collecting signatures at once
        #: would let one set of hands print twice.
        Index(
            "uq_emission_open_city",
            "city_id",
            unique=True,
            postgresql_where=text("state = 'open'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("city.id"), nullable=False)
    proposer_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )
    #: The sum to print, in minor units.
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[EmissionState] = enum_column(
        EmissionState, "emission_state", nullable=False, default=EmissionState.OPEN
    )
    created_at: Mapped[datetime] = created_column()
    #: Past this moment the proposal cannot be signed; it is marked expired by
    #: the next proposal that comes to the counter.
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    printed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class EmissionSignature(Base):
    """One holder's signature under one proposal. A hand signs once."""

    __tablename__ = "emission_signature"
    __table_args__ = (UniqueConstraint("proposal_id", "identity_id", name="uq_emission_signature"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("emission_proposal.id"), nullable=False
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    created_at: Mapped[datetime] = created_column()
