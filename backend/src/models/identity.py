"""Аккаунт, личность, тело — три разные вещи, и путать их нельзя.

| Сущность | Теряется при смерти |
|---|---|
| Аккаунт — оплата, устройство, телеметрия | — |
| Личность — имя, репутация, знания, счёт, гражданство | **Нет** |
| Тело — выносливость, инвентарь, местоположение, ранения | **Да, целиком** |

Отсюда всё поведение при смерти: рецепты и агротехника переживают гибель, а
инструменты, семена и монета в кошельке — нет (D-011, D-012, D-033, 09-death).

Один аккаунт — одна личность (D-011). Имя сменить нельзя, поэтому репутация
неотчуждаема.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Numeric, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class Account(Base):
    """Оплата и устройство. Игровых свойств не имеет намеренно."""

    __tablename__ = "account"

    id: Mapped[uuid.UUID] = uuid_pk()
    created_at: Mapped[datetime] = created_column()
    disabled_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Identity(Base):
    """Личность. Неуничтожима: хранится в Сети на всех биопринтерах сразу."""

    __tablename__ = "identity"

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id"), unique=True, nullable=False
    )
    #: Имя сменить нельзя — на этом держится репутация (40-society/05).
    name: Mapped[str] = mapped_column(unique=True, nullable=False)
    citizenship_city_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = created_column()


class KnowledgeKind(StrEnum):
    """Четыре вида знания ведут себя одинаково: живут в личности, копируются
    на носитель, торгуются (05-domain-model)."""

    RECIPE = "recipe"
    PROPORTION = "proportion"
    AGROTECH = "agrotech"
    COMBINATION = "combination"
    PROGRAM = "program"


class Knowledge(Base):
    """Знание не отнимается ни смертью, ни судом, ни городом (инвариант И8)."""

    __tablename__ = "knowledge"
    __table_args__ = (
        UniqueConstraint("identity_id", "kind", "key", name="uq_knowledge_identity_key"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    kind: Mapped[KnowledgeKind] = enum_column(KnowledgeKind, "knowledge_kind", nullable=False)
    #: Имя рецепта из `build/recipes.json` либо имя сохранённой настройки.
    key: Mapped[str] = mapped_column(nullable=False)
    #: Первооткрыватель — имя навсегда привязано к рецепту (D-064).
    discovered: Mapped[bool] = mapped_column(nullable=False, default=False)
    acquired_at: Mapped[datetime] = created_column()


class BodyState(StrEnum):
    ALIVE = "alive"
    DEAD = "dead"


class Body(Base):
    """Оболочка. Конечна, и это тоже инвариант (И3)."""

    __tablename__ = "body"
    __table_args__ = (
        Index("ix_body_node_state", "node_id", "state"),
        Index("ix_body_identity_state", "identity_id", "state"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    state: Mapped[BodyState] = enum_column(
        BodyState, "body_state", nullable=False, default=BodyState.ALIVE
    )

    #: Выносливость. Потолка на сутки нет: ограничение экономическое —
    #: работать дольше значит больше есть (D-091).
    stamina: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)

    #: Гибернация: тело спит и восстанавливается офлайн (D-091). Пусто — бодр.
    sleeping_since: Mapped[datetime | None] = mapped_column(nullable=True)
    #: Заснул с кроватью: дома гибернация идёт быстрее. Флаг фиксируется при
    #: засыпании — кровать, привезённая к спящему, его сон не улучшает.
    sleeping_home: Mapped[bool] = mapped_column(nullable=False, default=False)

    #: Сытость: до этого момента расход выносливости снижен (D-119). Горячее
    #: не добавляет запаса — оно замедляет расход, и это не бафф, а обед.
    satiated_until: Mapped[datetime | None] = mapped_column(nullable=True)

    #: Когда тело заняло свой узел: печатью либо приходом. Раньше этого момента
    #: оно здесь ничего не слышало (D-043) — горизонт чата, а не биография.
    node_since: Mapped[datetime] = created_column()

    printed_at: Mapped[datetime] = created_column()
    died_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Wound(Base):
    """Ранение: замедляет и режет выносливость, лечится временем и перевязкой (D-096)."""

    __tablename__ = "wound"
    __table_args__ = (Index("ix_wound_body", "body_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    body_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("body.id"), nullable=False)
    #: Чем нанесено — для журнала и суда: «обрушение свода».
    cause: Mapped[str] = mapped_column(nullable=False)
    treated: Mapped[bool] = mapped_column(nullable=False, default=False)
    heals_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = created_column()
