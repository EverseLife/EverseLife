"""Энергия города: общий пул (D-071, D-082).

Внутри города энергию никуда не подводят: всё, что стоит на его территории,
питается из **одного пула**. Отдельных подключений, проводов и линий между
узлами города нет и не будет — упрощено распределение, а не дефицит.

**Пул принадлежит городу**, а города как института ещё нет: он приезжает с
Э3 вместе с уставом и казной. До тех пор пул живёт на узле-представителе
города — том самом, чьими детьми в иерархии показа является городская
застройка (`Node.parent_id`). Территория города = его дети, и это ровно то,
чем город и является на карте сегодня. С появлением `City` пул переедет на
него без изменения смысла: перепривязать ключ.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class EnergyPool(Base):
    """Заряд города и момент, до которого он посчитан."""

    __tablename__ = "energy_pool"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: Узел-представитель города. Один пул на город.
    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node.id"), nullable=False, unique=True
    )

    #: Сколько энергии в пуле сейчас.
    stored: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False, default=0)
    #: До какого момента выработка уже начислена. Производство идёт временем, а
    #: не кликом: тик доводит пул до «сейчас» и двигает эту метку.
    counted_at: Mapped[datetime] = created_column()

    #: Тариф отпуска, ТК за 100 энергии. Уставом города правится с Э3 (D-085);
    #: до тех пор здесь лежит умолчание вольта, и оно же видно игрокам.
    tariff: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    created_at: Mapped[datetime] = created_column()
