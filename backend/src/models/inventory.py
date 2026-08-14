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

from src.db.base import Base, created_column, enum_column, uuid_pk


class ContainerKind(StrEnum):
    """Где предмет лежит. Материя перемещается только физически (D-047)."""

    #: Инвентарь тела. Гибнет вместе с телом.
    BODY = "body"
    #: То, что стоит в узле под открытым небом: станки, изделия у станка.
    #: Временный дом для станков: со зданиями (Э3) они переедут в `BUILDING`,
    #: потому что станок задаёт, чем здание является (D-106).
    NODE = "node"
    #: Здание: склад, мастерская, двор.
    BUILDING = "building"
    #: Товар личности, загруженный в терминал маркетплейса узла. Загрузка
    #: физическая, распоряжение — удалённое (D-047).
    MARKET = "market"
    #: Груз транспорта.
    VEHICLE = "vehicle"
    #: Добытое за текущую сессию: ещё не в инвентаре, теряется при обвале.
    MINING_SESSION = "mining_session"


class Container(Base):
    __tablename__ = "container"
    __table_args__ = (Index("ix_container_owner", "kind", "owner_id", "node_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[ContainerKind] = enum_column(ContainerKind, "container_kind", nullable=False)
    #: Тело, здание, личность или сессия — в зависимости от вида.
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: Где лежит, если это привязано к месту. У инвентаря тела пусто: тело носит
    #: своё с собой. У товара в терминале обязательно — маркетплейс всегда
    #: местный, глобального рынка нет и не будет (D-003).
    node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
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
        CheckConstraint("fineness IS NULL OR (fineness > 0 AND fineness <= 1000)",
                        name="fineness_in_permille"),
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

    #: Проба монеты в тысячных (D-016). Качества у монеты нет вовсе: её
    #: описывает содержание металла, а клеймо эмитента — это `maker_identity_id`.
    #: Эмитент вправе занизить пробу, оставив разницу себе, — и это видно здесь.
    fineness: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)

    #: У аккумулятора: сколько в нём энергии и когда это записано (D-071).
    #: Энергия не лежит в мешке — она либо в пуле города, либо здесь, и отсюда
    #: медленно утекает: `energy.battery_selfdischarge` в сутки.
    charge: Mapped[float | None] = mapped_column(Numeric(12, 3), nullable=True)
    charged_at: Mapped[datetime | None] = mapped_column(nullable=True)

    #: У семян: чей это сорт и насколько партия сохранила его силу, % (D-057).
    #: Сила падает у пересеваемого без отбора семенного фонда — и это то, из-за
    #: чего селекционер нужен даже там, где новых сортов не выводят (D-067).
    variety_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    vigor: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)

    #: У станка: чьё тело за ним работает сейчас (D-150). Пока идёт партия,
    #: станок занят и второму не отдаётся: мастерская — это столько мест,
    #: сколько станков, а не бесплатный цех на весь город.
    busy_body_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: До какого момента занят. Метка страхует от вечной занятости, если
    #: партия исчезнет мимо своего задания.
    busy_until: Mapped[datetime | None] = mapped_column(nullable=True)

    #: Вид блюда: сочетание решает вид, а не качество (D-128). По виду считается
    #: разнообразие рациона (D-105). У не-еды пусто.
    flavor: Mapped[str | None] = mapped_column(nullable=True)
    #: Доля закрытых ролей: полный обед держит сытость дольше (D-128).
    roles_filled: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)

    created_at: Mapped[datetime] = created_column()
