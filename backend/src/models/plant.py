# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A cultivar -- what is inherited by seeds (D-057, D-067).

A crop (spelt, turnip) is set by the vault and immutable. A **cultivar** is a
specific line within a crop: it has its own numbers, its own author and its
own history. A crop's base cultivar is created lazily by the engine and
belongs to everyone; everything else is bred by players by crossing.

The distinction the whole seed economy rests on:

* **hybrid** (`stable = False`) -- obtained at once and often better than the
  parents, but its seeds segregate: the next generation loses strength;
* **cultivar** (`stable = True`) -- a hybrid brought to constancy by selection.
  Gives the same thing time after time, and therefore sells once and for all.

Traits are stored as numbers of the same nature as in `build/plants.json` --
so that a cultivar can substitute for the crop without unit conversion.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Variety(Base):
    """A line within a crop: base, hybrid or a bred cultivar."""

    __tablename__ = "variety"
    __table_args__ = (
        Index("ix_variety_culture", "culture_id"),
        Index("ix_variety_author", "author_identity_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    #: The crop from `build/plants.json`: it decides what the cultivar gives and what it is sown
    #: with.
    culture_id: Mapped[str] = mapped_column(nullable=False)
    #: The name is given by the creator and attached forever -- like a
    #: craftsman's mark. Empty for a nameless hybrid until stabilisation.
    name: Mapped[str | None] = mapped_column(nullable=True)
    #: The author. Empty for a crop's base cultivar: it is nobody's.
    author_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )
    parent_a_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    parent_b_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    #: Generations of selection passed. Stabilisation is exactly them (D-067).
    generation: Mapped[int] = mapped_column(nullable=False, default=0)
    #: Whether stable: a cultivar's seeds give the same thing, a hybrid's do not.
    stable: Mapped[bool] = mapped_column(nullable=False, default=True)

    #: The cultivar's numbers: yield, cycle, required fertility, spoilage, temper.
    #: The units are the same as the crop's in `build/plants.json`.
    traits: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    created_at: Mapped[datetime] = created_column()


class Nursery(Base):
    """An ongoing crossing: the nursery needs a full growth cycle (D-057).

    A separate table, not a craft batch: a crossing has neither a machine
    with quality nor a spread -- it has two parents and a deadline.
    """

    __tablename__ = "nursery"
    __table_args__ = (Index("ix_nursery_body", "body_id", "done"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    body_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("body.id"), nullable=False)
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)

    parent_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variety.id"), nullable=False)
    parent_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variety.id"), nullable=False)
    #: How many seeds come out if it sprouts.
    seeds: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)

    done: Mapped[bool] = mapped_column(nullable=False, default=False)
    #: What came out. Empty on failure -- a too similar cultivar does not sprout (D-067).
    result_variety_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    started_at: Mapped[datetime] = created_column()
    ready_at: Mapped[datetime] = mapped_column(nullable=False)
