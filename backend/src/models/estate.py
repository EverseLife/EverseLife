"""Недвижимость: здание на участке и ценная бумага на владение.

**Здание** — то, что строят на участке до всякого станка (D-106, D-125):
станок ставится в здание и занимает его площадь, поэтому площадь дома — не
украшение, а вместимость: `build.slots_per_area` квадратных метров на одно
рабочее место.

**Ценная бумага** — электронный документ о владении участком. Выдаётся при
выкупе городской земли и при занятии дикой; живёт в Сети, а не в кармане:
смерть тела её не трогает (D-012), а продаётся она договором купли-продажи —
удалённо, как всякий документ (D-116).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Building(Base):
    """Здание на участке. Пока одно на узел и одной ступени прочности.

    Этажность и ступени прочности объявлены вольтом (`build.strength_levels`,
    `build.floors_by_strength`) и приедут своей механикой; таблица заводится
    так, чтобы их появление было доложением колонок, а не переделкой.
    """

    __tablename__ = "building"
    __table_args__ = (
        Index("ix_building_node", "node_id"),
        CheckConstraint("area_m2 > 0", name="area_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)

    #: Площадь застройки, м². Не больше площади участка: двор — остаток.
    area_m2: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    built_at: Mapped[datetime] = created_column()


class Deed(Base):
    """Ценная бумага на владение участком.

    Владелец бумаги и есть владелец узла: `node.owner_identity_id` — то же
    самое, продублированное для быстрых проверок движка, и меняются они вместе.
    """

    __tablename__ = "deed"
    __table_args__ = (
        Index("ix_deed_owner", "owner_identity_id"),
        CheckConstraint("sale_price IS NULL OR sale_price > 0", name="sale_price_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    #: Один участок — одна бумага: вторая бумага на тот же узел — подделка.
    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node.id"), nullable=False, unique=True
    )
    owner_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )

    #: Почём выдана: цена выкупа минорными единицами, ноль у занятой дикой земли.
    paid: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    #: Выставлена на продажу: цена и, если договор адресный, покупатель.
    #: Пусто — бумага не продаётся. Продажа удалённая: документ живёт в Сети.
    sale_price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sale_to_identity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    issued_at: Mapped[datetime] = created_column()
