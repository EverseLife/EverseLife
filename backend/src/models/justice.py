"""Суд: дело и санкция (D-095, D-117, D-166).

Дело — запись «истец обвиняет ответчика в этом городе». Санкция — то, что
движок исполняет по приговору, и она отдельной строкой: у срочных санкций есть
конец, и снимать их обязано задание журнала, а не память судьи.
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
    #: Приговор вынесен: санкция применена.
    JUDGED = "judged"
    #: Отказано: оправдание — тоже приговор, и висящих дел не бывает.
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
    #: Суть претензии словами: движок её не осмысляет — это работа судьи.
    claim: Mapped[str] = mapped_column(nullable=False)
    #: Пошлина, ушедшая в казну при подаче, минорными единицами.
    fee: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    state: Mapped[CaseState] = enum_column(
        CaseState, "case_state", nullable=False, default=CaseState.OPEN
    )
    judge_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )
    #: Чем кончилось — словами приговора, для карточки дела.
    verdict: Mapped[str | None] = mapped_column(nullable=True)
    opened_at: Mapped[datetime] = created_column()
    judged_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Sanction(Base):
    """Применённая санкция. Срочная снимается заданием журнала, а не памятью."""

    __tablename__ = "sanction"
    __table_args__ = (
        Index("ix_sanction_target", "identity_id", "lifted_at"),
        Index("ix_sanction_city", "city_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("court_case.id"), nullable=True
    )
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("city.id"), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )
    #: Примитив из `laws.json`: движок не держит своего списка (D-094).
    kind: Mapped[str] = mapped_column(nullable=False)
    #: У штрафа — сколько присуждено; у заключения — узел, в котором держат.
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    node_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("node.id"), nullable=True
    )
    #: Что не удалось взыскать: долг перед городом ждёт своей механики.
    debt: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    created_at: Mapped[datetime] = created_column()
    #: До какого момента действует. Пусто — бессрочно.
    until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lifted_at: Mapped[datetime | None] = mapped_column(nullable=True)
