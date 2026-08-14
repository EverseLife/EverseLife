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

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class Planet(StrEnum):
    TERRA = "terra"
    AQUATICA = "aquatica"
    PYROXIS = "pyroxis"
    AURORA = "aurora"


class Layer(StrEnum):
    """Слой карты, на котором узел показывается (D-045, D-097).

    Мир — один граф локаций; слои — абстракция показа, а не устройство мира.
    Узлы верхних слоёв (планета на космосе, город на планете) — представители
    групп: у них есть дети, но по ним не ходят — ходят по листьям.
    """

    #: Планеты и корабли: то, что видно из космоса.
    SPACE = "space"
    #: Города и крупные одиночные локации планеты.
    PLANET = "planet"
    #: Городская застройка: кольца вокруг биопринтера (D-089).
    CITY = "city"
    #: Субузлы локации: этажи дома, комнаты комплекса.
    LOCATION = "location"


class Node(Base):
    __tablename__ = "node"
    __table_args__ = (Index("ix_node_parent", "parent_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    #: Устойчивый ключ для ссылок из данных и тестов: `terra.capital`.
    key: Mapped[str] = mapped_column(unique=True, nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    planet: Mapped[Planet] = enum_column(Planet, "planet", nullable=False)

    #: На каком слое узел показывается. Ходят по листьям; узел с детьми —
    #: представитель группы на своём слое.
    layer: Mapped[Layer] = enum_column(Layer, "node_layer", nullable=False, default=Layer.CITY)
    #: Группа, в которую узел входит: локация → город → планета. Иерархия
    #: показа поверх графа, а не второй граф.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("node.id"), nullable=True)

    #: Площадь участка, м². Разыгрывается при появлении узла (D-125).
    area_m2: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    #: Свойства места: температура, осадки, вода, плодородие, ветер, лес.
    #: Разыгрываются при генерации, сумма достоинств ограничена (D-126).
    #: Лежат картой, потому что состав свойств ещё будет меняться.
    properties: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    owner_city_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: Хозяин участка: землю занимают присутственно, в диком узле (06-farming).
    #: Титул продаётся меной наравне с вещами (D-116) — когда мена приедет.
    owner_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )
    created_at: Mapped[datetime] = created_column()


class Surface(StrEnum):
    """Покрытие ребра решает и время, и саму возможность проехать (D-107)."""

    #: Бездорожье: вдвое-втрое дольше, транспорт не проходит вовсе.
    TRAIL = "trail"
    #: Дорога — эталон времени.
    ROAD = "road"
    #: Мощёный тракт: быстрее, держит тяжёлый транспорт.
    PAVED = "paved"


class Edge(Base):
    """Ребро графа: маршрут со свойствами, а не просто связь (10-world/07).

    Ребро **ненаправленное**: дорога одинакова в обе стороны. Хранится одной
    строкой, а поиск идёт по обоим концам — иначе рано или поздно появится
    ребро, ведущее только туда.
    """

    __tablename__ = "edge"
    __table_args__ = (
        Index("ix_edge_a", "node_a_id"),
        Index("ix_edge_b", "node_b_id"),
        UniqueConstraint("node_a_id", "node_b_id", name="uq_edge_pair"),
        CheckConstraint("node_a_id <> node_b_id", name="edge_not_a_loop"),
        CheckConstraint("base_seconds > 0", name="edge_takes_time"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    node_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    node_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)

    #: Сколько идти пешком по дороге. Секунды: шаг внутри города и переход
    #: между узлами живут в одной величине, а не в двух разных единицах.
    base_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    surface: Mapped[Surface] = enum_column(
        Surface, "edge_surface", nullable=False, default=Surface.ROAD
    )
    #: Состояние покрытия, 0…100 (D-158). Падает на `road.decay_rate` в сутки;
    #: на нуле покрытие опускается на ступень, а состояние начинается заново.
    #: У бездорожья смысла не имеет: зарастать там нечему.
    condition: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=100)
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
