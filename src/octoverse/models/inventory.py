"""Предметы и то, что их вмещает.

Два числа 0…100 на предмете, и их путают чаще всего (15-quality):

* **качество** — каким предмет сделан. Не меняется никогда;
* **состояние** — насколько изношен сейчас. Начинается со 100.

Качество определяет, как быстро падает состояние и насколько предмет эффективен
в каждый момент. Клеймо обязательно: каждое изделие помнит мастера, и это делает
репутацию осязаемой (D-058).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from octoverse.db.base import Base, created_column, enum_column, uuid_pk


class ContainerKind(StrEnum):
    """Где предмет лежит. Материя перемещается только физически (D-047)."""

    #: Инвентарь тела. Гибнет вместе с телом.
    BODY = "body"
    #: Здание: склад, мастерская, двор.
    BUILDING = "building"
    #: Товар, загруженный в терминал маркетплейса. Загрузка физическая,
    #: распоряжение — удалённое.
    MARKET = "market"
    #: Груз транспорта.
    VEHICLE = "vehicle"
    #: Добытое за текущую сессию: ещё не в инвентаре, теряется при обвале.
    MINING_SESSION = "mining_session"


class Container(Base):
    __tablename__ = "container"
    __table_args__ = (Index("ix_container_owner", "kind", "owner_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[ContainerKind] = enum_column(ContainerKind, "container_kind", nullable=False)
    #: Тело, здание, терминал или сессия — в зависимости от вида.
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = created_column()


class Item(Base):
    """Стопка одинакового сырья либо одно изделие.

    Сырьё складывается, изделия — нет (04-items). Признак берётся из
    `build/recipes.json`: `kind` рецепта определяет поведение (D-090).
    """

    __tablename__ = "item"
    __table_args__ = (
        Index("ix_item_container", "container_id"),
        Index("ix_item_type", "type_key"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("quality IS NULL OR (quality >= 0 AND quality <= 100)",
                        name="quality_in_scale"),
        CheckConstraint("condition >= 0 AND condition <= 100", name="condition_in_scale"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    container_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("container.id"), nullable=False)

    #: Имя из `build/recipes.json` — рецепт либо сырьё.
    type_key: Mapped[str] = mapped_column(nullable=False)

    #: Внутренние единицы (`units.AMOUNT_SCALE`). У изделия всегда одна штука.
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: Каким сделан. У сырья определяется жилой и работой в забое.
    quality: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    #: Насколько изношен сейчас.
    condition: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=100)
    #: Потолок состояния. Падает при каждом ремонте на `quality.repair_ceiling_loss`,
    #: поэтому предмет всё равно конечен (столп П2, D-129).
    condition_cap: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=100)

    #: Клеймо: кто, когда и где изготовил (D-058).
    maker_identity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    made_at: Mapped[datetime | None] = mapped_column(nullable=True)
    made_node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    #: Для еды: когда испортится. Готовое портится в `cook.spoilage_multiplier`
    #: раз быстрее сырья (D-119).
    spoils_at: Mapped[datetime | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = created_column()
