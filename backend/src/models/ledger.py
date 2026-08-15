"""Money: double entry, not a "balance" field.

D-127 demands: the seller gets exactly what the buyer paid, minus tax and
commission. On a `balance` field this is unverifiable -- a discrepancy shows up
a month later and cannot be explained (01-tech-notes, pattern 2).

So money as a player attribute does not exist. What exists is an immutable
journal of postings; the balance is the sum over the account. Every deal, tax,
duty, salary and energy bill is a posting with a reference to its ground.

**The invariant the whole construction holds:** the sum of postings of each
operation is zero. Money moves, it does not appear. The only exception is
`genesis`-kind accounts, and a separate invariant check watches them: any
growth of the money supply must be explained.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, Index, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, created_column, enum_column, uuid_pk


class Currency(StrEnum):
    """The two forms of money, separated by place, not by planet (D-086)."""

    #: Terracoin -- Terra's cashless settlement.
    TK = "TK"


class AccountKind(StrEnum):
    #: An identity's account. Survives the body's death (D-012): money in the
    #: bank is intact, the coin on your person is not.
    IDENTITY = "identity"
    #: A city treasury (D-127, D-134).
    CITY_TREASURY = "city_treasury"
    #: A reservation deposit and an order's escrow -- money has left the payer
    #: but has not yet reached the recipient (D-116).
    ESCROW = "escrow"
    #: The banking system's reserve (D-087, D-167): money that left circulation.
    #: Credit is issued from here, repayment returns here -- not into circulation.
    BANK_RESERVE = "bank_reserve"
    #: The only lawful source and sink of the money supply. Every posting here
    #: is the subject of separate examination and telemetry.
    GENESIS = "genesis"


class PostingReason(StrEnum):
    """The posting's ground. Also what the court will see."""

    GENESIS = "genesis"
    TRADE = "trade"
    TAX_TRADE = "tax_trade"
    MARKET_FEE = "market_fee"
    DUTY = "duty"
    SALARY = "salary"
    UPKEEP = "upkeep"
    ENERGY_BILL = "energy_bill"
    COURT_FEE = "court_fee"
    FINE = "fine"
    ESCROW_HOLD = "escrow_hold"
    ESCROW_RELEASE = "escrow_release"
    LOAN = "loan"
    LOAN_REPAYMENT = "loan_repayment"
    #: Cancelled by D-175: the value remains for old postings.
    SEIGNIORAGE = "seigniorage"
    #: The city's margin on its borrower's interest (D-175).
    BANK_MARGIN = "bank_margin"
    #: A person-to-person transfer: pay for work, chip in, return a debt (D-190).
    TRANSFER = "transfer"


class LedgerAccount(Base):
    __tablename__ = "ledger_account"
    __table_args__ = (
        UniqueConstraint("kind", "owner_id", "currency", name="uq_ledger_account_owner"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[AccountKind] = enum_column(AccountKind, "ledger_account_kind", nullable=False)
    #: An identity, a city or another owner. `genesis` has no owner.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    currency: Mapped[Currency] = enum_column(Currency, "currency", nullable=False)
    created_at: Mapped[datetime] = created_column()


class LedgerTransaction(Base):
    """A whole operation. The postings inside it must sum to zero."""

    __tablename__ = "ledger_transaction"

    id: Mapped[uuid.UUID] = uuid_pk()
    at: Mapped[datetime] = created_column()
    reason: Mapped[PostingReason] = enum_column(PostingReason, "posting_reason", nullable=False)
    #: The grounding event. Through it the operation is linked to what happened
    #: in the world: an order fill, a verdict, an energy bill.
    event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    memo: Mapped[dict] = mapped_column(nullable=False, default=dict)

    entries: Mapped[list[LedgerEntry]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan", lazy="selectin"
    )


class LedgerEntry(Base):
    """One side of a posting. Sign: a debit is negative, a credit is positive."""

    __tablename__ = "ledger_entry"
    __table_args__ = (Index("ix_ledger_entry_account", "account_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_transaction.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_account.id"), nullable=False
    )
    #: Minor units (`units.MONEY_SCALE`). Integer -- so that not a cent is lost.
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)

    transaction: Mapped[LedgerTransaction] = relationship(back_populates="entries")
