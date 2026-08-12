"""Узел — атом мира, и жила внутри него.

Узел — это не «место, где стоит здание», а само здание: локация, лист графа,
точка, куда приходят (D-089, 10-world/07-map-topology). В узле ровно одна
постройка **или** жила **или** ничего — это инвариант целостности, а не
пожелание (05-domain-model, И4).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from octoverse.db.base import Base, created_column, enum_column, uuid_pk


class Planet(StrEnum):
    TERRA = "terra"
    AQUATICA = "aquatica"
    PYROXIS = "pyroxis"
    AURORA = "aurora"


class Node(Base):
    __tablename__ = "node"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: Устойчивый ключ для ссылок из данных и тестов: `terra.capital`.
    key: Mapped[str] = mapped_column(unique=True, nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    planet: Mapped[Planet] = enum_column(Planet, "planet", nullable=False)

    #: Площадь участка, м². Разыгрывается при появлении узла (D-125).
    area_m2: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    #: Свойства места: температура, осадки, вода, плодородие, ветер, лес.
    #: Разыгрываются при генерации, сумма достоинств ограничена (D-126).
    #: Лежат картой, потому что состав свойств ещё будет меняться.
    properties: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    owner_city_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = created_column()


class Vein(Base):
    """Жила: порода, запас, богатство. Жилы конечны — это неотменяемо (столп П2)."""

    __tablename__ = "vein"
    __table_args__ = (
        Index("ix_vein_node", "node_id"),
        CheckConstraint("remaining >= 0", name="remaining_non_negative"),
        CheckConstraint("richness >= 0 AND richness <= 100", name="richness_in_scale"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)

    #: Порода — имя сырья из `build/recipes.json` («Руда», «Уголь», «Камень»).
    resource: Mapped[str] = mapped_column(nullable=False)

    #: Богатство 0…100. Задаёт выход, качество сырья и устойчивость свода.
    #: Падает по мере выработки на `vein.richness_decay` за `vein.depletion_step`.
    richness: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)

    #: Оставшийся запас во внутренних единицах (`units.AMOUNT_SCALE`).
    #: Дошёл до нуля — жила исчезает, и шахтёрский город вместе с ней.
    remaining: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: Сколько всего выбрано — по этому считаются ступени истощения.
    extracted: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    created_at: Mapped[datetime] = created_column()
    depleted_at: Mapped[datetime | None] = mapped_column(nullable=True)
