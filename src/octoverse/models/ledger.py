"""Деньги: двойная запись, а не поле «баланс».

D-127 требует: продавец получает ровно то, что заплатил покупатель, минус налог
и комиссия. На поле `balance` это непроверяемо — расхождение обнаружится через
месяц и не будет объяснимо (01-tech-notes, паттерн 2).

Поэтому денег как атрибута игрока не существует. Существует неизменяемый журнал
проводок; баланс — сумма по счёту. Каждая сделка, налог, пошлина, жалованье и
счёт за энергию — проводка со ссылкой на основание.

**Инвариант, который держит вся конструкция:** сумма проводок каждой операции
равна нулю. Деньги переходят, а не появляются. Единственное исключение — счета
вида `genesis`, и на них смотрит отдельная проверка инвариантов: любой прирост
денежной массы обязан быть объяснён.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, Index, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from octoverse.db.base import Base, created_column, enum_column, uuid_pk


class Currency(StrEnum):
    """Две формы денег, разведённые по местам, а не по планетам (D-086)."""

    #: Терракоин — безналичный расчёт Терры.
    TK = "TK"


class AccountKind(StrEnum):
    #: Счёт личности. Переживает гибель тела (D-012): деньги в банке целы,
    #: монета при себе — нет.
    IDENTITY = "identity"
    #: Казна города (D-127, D-134).
    CITY_TREASURY = "city_treasury"
    #: Задаток по брони и эскроу заказа — деньги вышли у плательщика,
    #: но ещё не пришли получателю (D-116).
    ESCROW = "escrow"
    #: Единственный законный источник и сток денежной массы. Каждая проводка
    #: сюда — предмет отдельного разбора и телеметрии.
    GENESIS = "genesis"


class PostingReason(StrEnum):
    """Основание проводки. Оно же — то, что увидит суд."""

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


class LedgerAccount(Base):
    __tablename__ = "ledger_account"
    __table_args__ = (
        UniqueConstraint("kind", "owner_id", "currency", name="uq_ledger_account_owner"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[AccountKind] = enum_column(AccountKind, "ledger_account_kind", nullable=False)
    #: Личность, город или иной владелец. У `genesis` владельца нет.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    currency: Mapped[Currency] = enum_column(Currency, "currency", nullable=False)
    created_at: Mapped[datetime] = created_column()


class LedgerTransaction(Base):
    """Операция целиком. Проводки внутри неё обязаны давать в сумме ноль."""

    __tablename__ = "ledger_transaction"

    id: Mapped[uuid.UUID] = uuid_pk()
    at: Mapped[datetime] = created_column()
    reason: Mapped[PostingReason] = enum_column(PostingReason, "posting_reason", nullable=False)
    #: Событие-основание. По нему операция связывается с тем, что произошло
    #: в мире: исполнение ордера, приговор, счёт за энергию.
    event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    memo: Mapped[dict] = mapped_column(nullable=False, default=dict)

    entries: Mapped[list[LedgerEntry]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan", lazy="selectin"
    )


class LedgerEntry(Base):
    """Одна сторона проводки. Знак: списание отрицательно, зачисление положительно."""

    __tablename__ = "ledger_entry"
    __table_args__ = (Index("ix_ledger_entry_account", "account_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_transaction.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_account.id"), nullable=False
    )
    #: Минорные единицы (`units.MONEY_SCALE`). Целое — чтобы копейка не терялась.
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)

    transaction: Mapped[LedgerTransaction] = relationship(back_populates="entries")
