"""Сорт — то, что наследуется семенами (D-057, D-067).

Культура (полба, репа) задана вольтом и неизменна. **Сорт** — конкретная линия
внутри культуры: у неё свои числа, свой автор и своя история. Базовый сорт
культуры заводится движком лениво и принадлежит всем; всё остальное выводят
игроки скрещиванием.

Различение, на котором держится вся экономика семян:

* **гибрид** (`stable = False`) — получается сразу и часто лучше родителей, но
  его семена расщепляются: следующее поколение теряет силу;
* **сорт** (`stable = True`) — гибрид, доведённый отбором до постоянства. Даёт
  то же самое из раза в раз, и потому продаётся один раз навсегда.

Признаки хранятся числами той же природы, что в `build/plants.json`, — чтобы
сорт можно было подставить на место культуры без пересчёта единиц.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Variety(Base):
    """Линия внутри культуры: базовая, гибрид или выведенный сорт."""

    __tablename__ = "variety"
    __table_args__ = (
        Index("ix_variety_culture", "culture_id"),
        Index("ix_variety_author", "author_identity_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    #: Культура из `build/plants.json`: она решает, что сорт даёт и чем сеется.
    culture_id: Mapped[str] = mapped_column(nullable=False)
    #: Имя даёт создатель, и оно закрепляется навсегда — как клеймо мастера.
    #: У безымянного гибрида пусто до стабилизации.
    name: Mapped[str | None] = mapped_column(nullable=True)
    #: Автор. У базового сорта культуры пусто: он ничей.
    author_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )
    parent_a_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    parent_b_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    #: Поколений отбора пройдено. Стабилизация — это они и есть (D-067).
    generation: Mapped[int] = mapped_column(nullable=False, default=0)
    #: Постоянен ли: семена сорта дают то же самое, семена гибрида — нет.
    stable: Mapped[bool] = mapped_column(nullable=False, default=True)

    #: Числа сорта: урожайность, цикл, требуемое плодородие, порча, характер.
    #: Единицы те же, что у культуры в `build/plants.json`.
    traits: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    created_at: Mapped[datetime] = created_column()


class Nursery(Base):
    """Идущее скрещивание: питомнику нужен полный цикл роста (D-057).

    Отдельная таблица, а не партия крафта: у скрещивания нет ни станка с
    качеством, ни разброса — у него есть два родителя и срок.
    """

    __tablename__ = "nursery"
    __table_args__ = (Index("ix_nursery_body", "body_id", "done"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    body_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("body.id"), nullable=False)
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)

    parent_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variety.id"), nullable=False)
    parent_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variety.id"), nullable=False)
    #: Сколько семян выйдет, если взойдёт.
    seeds: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)

    done: Mapped[bool] = mapped_column(nullable=False, default=False)
    #: Что вышло. Пусто при неудаче — слишком похожий сорт не всходит (D-067).
    result_variety_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    started_at: Mapped[datetime] = created_column()
    ready_at: Mapped[datetime] = mapped_column(nullable=False)
