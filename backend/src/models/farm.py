# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The plot -- the unit of agronomy (D-118).

One crop, one term, one state, one round. Land is measured in metres: the
owner cuts the parcel into plots and seeks the balance themselves --
splitting is expensive (a round for each), merging is risky (disease and monoculture).

**Fertility and crop history belong to the land, not the layout** (I5): on a
split both parts inherit them as is, on a merge fertility is taken weighted
and history as the heaviest. Without that redrawing borders would be a free
depletion reset.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class PlotState(StrEnum):
    #: Unsown and unploughed. Only such a plot can be resurveyed.
    IDLE = "idle"
    #: Being ploughed: a long-running action, goes as a journal job.
    PLOWING = "plowing"
    #: Ploughed, ready for sowing.
    PLOWED = "plowed"
    #: Growing. Ripeness is derived from time, it needs no separate state.
    SOWN = "sown"


class Plot(Base):
    __tablename__ = "plot"
    __table_args__ = (
        Index("ix_plot_node", "node_id"),
        Index("ix_plot_owner", "owner_identity_id"),
        CheckConstraint("area_m2 > 0", name="area_positive"),
        CheckConstraint("fertility >= 0 AND fertility <= 100", name="fertility_in_scale"),
        CheckConstraint("moisture >= 0 AND moisture <= 100", name="moisture_in_scale"),
        CheckConstraint("health >= 0 AND health <= 100", name="health_in_scale"),
        CheckConstraint("growth >= 0 AND growth <= 100", name="growth_in_scale"),
        CheckConstraint("weeds >= 0 AND weeds <= 100", name="weeds_in_scale"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    #: Who surveyed it. Land title and rent arrive with cities (E3).
    owner_identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)

    name: Mapped[str] = mapped_column(nullable=False)
    area_m2: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    state: Mapped[PlotState] = enum_column(
        PlotState, "plot_state", nullable=False, default=PlotState.IDLE
    )

    #: Fertility of the land under this layout, 0..100.
    fertility: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)

    #: History: what grew last and how many cycles in a row. Monoculture
    #: depletion (`farm.soil_depletion`) is counted from it.
    last_culture: Mapped[str | None] = mapped_column(nullable=True)
    same_culture_cycles: Mapped[int] = mapped_column(nullable=False, default=0)

    #: What grows now -- a crop id from `build/plants.json`.
    culture_id: Mapped[str | None] = mapped_column(nullable=True)
    #: Whose cultivar is sown and with what strength the seed was: the harvest
    #: is computed from them, not from the crop's numbers (D-057).
    variety_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    seed_vigor: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    sown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: The three scales of a growing bed (D-293), as of `settled_at`: what
    #: happened since is a pure function of the elapsed time (`farm/life.py`),
    #: computed by every read, written by every action and by the world tick.
    #: None is shown as a number: the player reads a stage and a word.
    moisture: Mapped[float] = mapped_column(
        Numeric(6, 2), nullable=False, default=0, server_default="0"
    )
    health: Mapped[float] = mapped_column(
        Numeric(6, 2), nullable=False, default=100, server_default="100"
    )
    growth: Mapped[float] = mapped_column(
        Numeric(6, 2), nullable=False, default=0, server_default="0"
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: A feeding's boost, per cent of speed, and the stage it lasts through.
    growth_boost: Mapped[float] = mapped_column(
        Numeric(6, 2), nullable=False, default=0, server_default="0"
    )
    boost_stage: Mapped[str | None] = mapped_column(nullable=True)
    #: What each stage of this sowing was fed and what it did: stage -> rows
    #: of `{goods, effect}`. The survey reads the current stage's rows for
    #: the signs; the harvest counts nothing from here.
    fed: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    #: Feedings repeated within one stage: each costs its share of the harvest.
    overfed: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    #: Weeds on the bed, 0-100 (D-295): up with the crop as of `settled_at`,
    #: cleared by a weeding. Whether this sowing was thinned: once, and early.
    weeds: Mapped[float] = mapped_column(
        Numeric(6, 2), nullable=False, default=0, server_default="0"
    )
    thinned: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    #: Since when the land stands fallow: recovery is credited by elapsed time
    #: on the next action -- it needs no tick.

    idle_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: The plough's progress, banked in minutes of work done: a plough is
    #: paused and taken up again with what was done kept (D-277), and
    #: dropped only by a separate decision. `plow_since` is when the current
    #: run began -- empty while the plough stands paused.
    plow_done_minutes: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, server_default="0"
    )
    plow_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = created_column()
