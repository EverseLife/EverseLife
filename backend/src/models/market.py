"""Книга заявок узла (D-003, D-047).

Глобального рынка нет и не будет: каждый маркетплейс — **отдельная книга в
конкретной локации** (столп П3). Цена — это то, что кто-то реально готов
заплатить, а не оценка движка: любая оценка товара движком есть скрытый NPC
(D-002).

Разделение, на котором держится вся конструкция: **материя требует присутствия,
распоряжение — нет**. Товар лежит в терминале физически, а ордер живёт на
сервере и управляется откуда угодно, хоть с другой планеты.

Торгуется товар **по ступеням качества** (D-058): «железная руда, хорошая» —
отдельная позиция в стакане. Непрерывная шкала сделала бы книгу нечитаемой и
убила бы ликвидность: никто не купит партию качества 63, если рядом лежит 64.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderState(StrEnum):
    ACTIVE = "active"
    #: Исполнен целиком.
    FILLED = "filled"
    #: Снят владельцем.
    CANCELLED = "cancelled"
    #: Истёк срок `market.order_lifetime`.
    EXPIRED = "expired"


class ReservationState(StrEnum):
    HELD = "held"
    #: Выкуплена: покупатель приехал и доплатил остаток.
    REDEEMED = "redeemed"
    #: Срок вышел: задаток остался продавцу, товар вернулся в стакан.
    LAPSED = "lapsed"


class Reservation(Base):
    """Бронь с задатком и сроком (D-047).

    Купить удалённо нельзя: иначе игрок скупает всё везде, товар зависает в
    резерве, а стаканы становятся фикцией. Разумное исключение — бронь: купец,
    собираясь в дорогу, резервирует партию, вносит `market.reservation_deposit`
    и обязан забрать до срока `market.reservation_period`.

    Не забрал — **задаток остаётся продавцу**, товар возвращается в стакан. Это
    даёт торговцам планировать рейсы, не создавая мёртвых резервов.
    """

    __tablename__ = "market_reservation"
    __table_args__ = (
        Index("ix_reservation_buyer", "buyer_identity_id", "state"),
        Index("ix_reservation_due", "state", "expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_order.id"), nullable=False
    )
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    buyer_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )
    seller_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )

    type_key: Mapped[str] = mapped_column(nullable=False)
    tier: Mapped[str] = mapped_column(nullable=False)
    #: Цена зафиксирована броней: за это и вносится задаток.
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deposit: Mapped[int] = mapped_column(BigInteger, nullable=False)

    state: Mapped[ReservationState] = enum_column(
        ReservationState, "reservation_state", nullable=False,
        default=ReservationState.HELD,
    )
    created_at: Mapped[datetime] = created_column()
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Order(Base):
    """Лимитная заявка. Рыночных нет намеренно: проще и честнее."""

    __tablename__ = "market_order"
    __table_args__ = (
        #: Рабочая выборка стакана: узел, товар, ступень, сторона.
        Index("ix_market_order_book", "node_id", "type_key", "tier", "side", "state"),
        Index("ix_market_order_owner", "identity_id", "state"),
        CheckConstraint("price > 0", name="price_positive"),
        CheckConstraint("amount_left >= 0", name="amount_left_non_negative"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    #: Ордер принадлежит личности, а не телу: тело смертно, обязательства нет.
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)

    side: Mapped[OrderSide] = enum_column(OrderSide, "order_side", nullable=False)
    type_key: Mapped[str] = mapped_column(nullable=False)
    #: Ступень качества из `quality.tiers` — отдельная позиция стакана (D-058).
    tier: Mapped[str] = mapped_column(nullable=False)

    #: Цена за единицу товара, минорные единицы денег (`units.MONEY_SCALE`).
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Объём во внутренних единицах количества (`units.AMOUNT_SCALE`).
    amount_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_left: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: Сколько денег ещё заморожено под этот ордер. Только у покупки: продавец
    #: замораживает товар, покупатель — деньги.
    escrowed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    state: Mapped[OrderState] = enum_column(
        OrderState, "order_state", nullable=False, default=OrderState.ACTIVE
    )

    created_at: Mapped[datetime] = created_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Trade(Base):
    """Состоявшаяся сделка.

    Хранится отдельно от ордеров: по ней считаются оборот города (D-100),
    справочная цена для пошлин (D-123) и торговая сводка (D-124). Ордер можно
    снять, сделку — нет.
    """

    __tablename__ = "market_trade"
    __table_args__ = (
        Index("ix_market_trade_book", "node_id", "type_key", "tier", "at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    #: У выкупленной брони встречной заявки нет вовсе: покупатель не выставлял
    #: ордер, он приехал и забрал зарезервированное (D-047).
    buy_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("market_order.id"), nullable=True
    )
    sell_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_order.id"), nullable=False
    )

    type_key: Mapped[str] = mapped_column(nullable=False)
    tier: Mapped[str] = mapped_column(nullable=False)
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: Налог с продажи платит продавец (D-127), комиссия — владельцу терминала.
    tax: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    fee: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    at: Mapped[datetime] = created_column()
