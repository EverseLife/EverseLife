"""Кредит и ключевая ставка (D-030, D-087, D-167).

Заём — договор: ставка фиксируется в момент выдачи и дальше не меняется, что бы
банк ни решил после. Решения банка живут отдельной строкой, чтобы историю
ставки можно было показать целиком: алгоритм публичен, значит и его прошлое
тоже.
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
    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )
    #: Выдано, минорными единицами.
    principal: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Сколько осталось вернуть: тело плюс начисленное.
    outstanding: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Ставка заёмщика, % годовых. Зафиксирована при выдаче: заём — договор,
    #: а не подписка на решения банка.
    rate: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    #: Через какой город выдан заём (D-175). Пусто — прямой заём столицы по
    #: худшей ставке: дешёвый кредит — привилегия гражданства.
    city_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("city.id"), nullable=True
    )
    #: Маржа города в ставке, % сверх ключевой. Хранится отдельно: при платеже
    #: процентов её доля уходит в казну города, остальное — в резерв столицы.
    margin: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)
    #: Сколько из выданного пришлось напечатать: датчик доли эмиссии (D-087).
    printed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Проценты: начислено и уплачено, нарастающим итогом (D-171). Платёж
    #: гасит сначала их, потом тело — иначе «доход системы» неизмерим.
    interest_accrued: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    interest_paid: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    state: Mapped[LoanState] = enum_column(
        LoanState, "loan_state", nullable=False, default=LoanState.OPEN
    )
    taken_at: Mapped[datetime] = created_column()
    #: До какого момента проценты уже начислены.
    accrued_at: Mapped[datetime] = created_column()
    #: Когда по займу платили в последний раз. От него считается просрочка
    #: (D-168): необслуживаемый долг — это не «старый», а «неоплачиваемый».
    serviced_at: Mapped[datetime] = created_column()
    repaid_at: Mapped[datetime | None] = mapped_column(nullable=True)


class RateDecision(Base):
    """Решение по ключевой ставке. Алгоритм публичен — значит и история тоже."""

    __tablename__ = "rate_decision"
    __table_args__ = (Index("ix_rate_at", "decided_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    rate: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    #: Что видели датчики: инфляция и доля эмиссии в выдаче, процентов.
    inflation: Mapped[float] = mapped_column(Numeric(8, 3), nullable=False, default=0)
    emission_share: Mapped[float] = mapped_column(
        Numeric(8, 3), nullable=False, default=0
    )
    #: Словами: почему получилось именно столько.
    why: Mapped[str] = mapped_column(nullable=False, default="")
    #: До какого момента ставка аварийно возвращена алгоритму (D-172). Это не
    #: наказание Совету, а предохранитель: цена ошибки — деньги у всех.
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = created_column()


class DefectReport(Base):
    """Репорт «дефектная печать» (D-173).

    По лору принтер иногда печатает людей без интеллекта. Репорт не убивает и
    не банит — он снижает доверие, а доверие режет кредитный лимит. Один от
    личности на личность: накрутка одним аккаунтом невозможна по построению.
    """

    __tablename__ = "defect_report"
    __table_args__ = (
        UniqueConstraint("reporter_identity_id", "target_identity_id",
                         name="uq_defect_report_pair"),
        Index("ix_defect_report_target", "target_identity_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    reporter_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )
    target_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )
    created_at: Mapped[datetime] = created_column()
