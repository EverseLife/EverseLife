# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The node's order book (D-003, D-047).

There is and will be no global market: each marketplace is **a separate book
in a specific location** (pillar P3). The price is what somebody is really
ready to pay, not the engine's valuation: any valuation of goods by the engine
is a hidden NPC (D-002).

The separation the whole construction rests on: **matter requires presence,
disposing does not**. Goods lie in the terminal physically, while an order
lives on the server and is managed from anywhere, even another planet.

Goods trade **by quality tiers** (D-058): "iron ore, good" is a separate
position in the book. A continuous scale would make the book unreadable and
kill liquidity: nobody buys a lot of quality 63 if 64 lies next to it.

The buyer, though, names a **floor** rather than a band (D-239): "iron, no
worse than 70" takes its own tier and every tier above it, and inside a tier
only the stacks that clear the floor. The seller still lists a tier -- what
they have is a lot, not a wish -- so the window stays five columns wide while
demand gathers on thresholds instead of scattering across five books.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderState(StrEnum):
    ACTIVE = "active"
    #: Filled entirely.
    FILLED = "filled"
    #: Cancelled by the owner.
    CANCELLED = "cancelled"
    #: The `market.order_lifetime` term expired.
    EXPIRED = "expired"


class ReservationState(StrEnum):
    HELD = "held"
    #: Redeemed: the buyer came and paid the remainder.
    REDEEMED = "redeemed"
    #: The term is up: the deposit stayed with the seller, the goods returned to the book.
    LAPSED = "lapsed"


class Reservation(Base):
    """A reservation with a deposit and a term (D-047).

    Remote buying is not allowed: otherwise a player buys everything everywhere,
    goods hang in reserve, and the books become a fiction. The reasonable
    exception is a reservation: a merchant preparing for the road reserves a
    lot, pays `market.reservation_deposit` and must collect before the
    `market.reservation_period` deadline.

    Did not collect -- **the deposit stays with the seller**, the goods return
    to the book. This lets traders plan trips without creating dead reserves.
    """

    __tablename__ = "market_reservation"
    __table_args__ = (
        Index("ix_reservation_buyer", "buyer_identity_id", "state"),
        Index("ix_reservation_due", "state", "expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market_order.id"), nullable=False)
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    buyer_identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    seller_identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)

    type_key: Mapped[str] = mapped_column(nullable=False)
    tier: Mapped[str] = mapped_column(nullable=False)
    #: The price is fixed by the reservation: that is what the deposit is paid for.
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deposit: Mapped[int] = mapped_column(BigInteger, nullable=False)

    state: Mapped[ReservationState] = enum_column(
        ReservationState,
        "reservation_state",
        nullable=False,
        default=ReservationState.HELD,
    )
    created_at: Mapped[datetime] = created_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Order(Base):
    """A limit order. There are no market ones on purpose: simpler and fairer."""

    __tablename__ = "market_order"
    __table_args__ = (
        #: The book's working selection: node, goods, tier, side.
        Index("ix_market_order_book", "node_id", "type_key", "tier", "side", "state"),
        #: Demand is not read by tier: a buy with a floor is a bid in every
        #: tier that can satisfy it (D-239), so the tier drops out of the key.
        Index("ix_market_order_demand", "node_id", "type_key", "side", "state"),
        Index("ix_market_order_owner", "identity_id", "state"),
        CheckConstraint("price > 0", name="price_positive"),
        CheckConstraint("amount_left >= 0", name="amount_left_non_negative"),
        CheckConstraint("min_quality IS NULL OR min_quality >= 0", name="min_quality_non_negative"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    #: The order belongs to the identity, not the body: the body is mortal, obligations are not.
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)

    side: Mapped[OrderSide] = enum_column(OrderSide, "order_side", nullable=False)
    type_key: Mapped[str] = mapped_column(nullable=False)
    #: Quality tier from `quality.tiers` -- a separate book position (D-058).
    #: For a sell it is the tier of the lot; for a buy it is where the order
    #: stands in the window, and what it will actually take is `min_quality`.
    tier: Mapped[str] = mapped_column(nullable=False)
    #: The buyer's floor: nothing worse than this quality is taken (D-239).
    #: Empty for a sell -- a seller offers a lot, not a demand. Empty, too, on
    #: a buy written before the floor existed: its tier's own start is read as
    #: the floor, which is exactly what its tier button meant.
    min_quality: Mapped[int | None] = mapped_column(nullable=True)

    #: Price per unit of goods, minor units of money (`units.MONEY_SCALE`).
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Volume in internal amount units (`units.AMOUNT_SCALE`).
    amount_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_left: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: How much money is still frozen under this order. Only for a buy: the
    #: seller freezes goods, the buyer money.
    escrowed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    state: Mapped[OrderState] = enum_column(
        OrderState, "order_state", nullable=False, default=OrderState.ACTIVE
    )

    created_at: Mapped[datetime] = created_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Trade(Base):
    """A concluded deal.

    Stored separately from orders: city turnover (D-100), the reference price
    for duties (D-123) and the trade summary (D-124) are computed from it. An
    order can be cancelled, a deal cannot.
    """

    __tablename__ = "market_trade"
    __table_args__ = (
        Index("ix_market_trade_book", "node_id", "type_key", "tier", "at"),
        #: The last deal per goods name, whatever its tier: the picker asks for
        #: it on every market event in the node, and deals are never deleted --
        #: without this the question sorts the node's whole trading history.
        #: The id sorts alongside the clock because `at` is the transaction's
        #: and one sweeping order stamps all its deals alike.
        Index(
            "ix_market_trade_last",
            "node_id",
            "type_key",
            text("at DESC"),
            text("id DESC"),
        ),
        #: The deals settled against one buy order: the statement opens a
        #: deposit row into them (D-190). Deals are never deleted, and a
        #: foreign key makes no index of its own -- without this every such
        #: opening reads the whole history of trade.
        Index("ix_market_trade_buy_order", "buy_order_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    #: A redeemed reservation has no opposing order at all: the buyer placed
    #: no order, they came and took what was reserved (D-047).
    buy_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("market_order.id"), nullable=True
    )
    sell_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market_order.id"), nullable=False)

    type_key: Mapped[str] = mapped_column(nullable=False)
    tier: Mapped[str] = mapped_column(nullable=False)
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: The sales tax is paid by the seller (D-127), the commission by the terminal owner.
    tax: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    fee: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    at: Mapped[datetime] = created_column()
