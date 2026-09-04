# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""a bed lives by three scales: moisture, health, growth

Care used to be a counter of credited days and a stamp of the last round
(D-263). With D-296 a sown bed carries its state instead: the moisture in the
ground, the health of the crop and the share grown, all as of `settled_at`,
plus the boost a feeding gave and what each stage was fed. What happened
since the stamp is a pure function of the elapsed time (`engine/farm/life`),
so a read computes it and an action or the world tick writes it.

The beds growing at the moment of this migration start their new life here:
half-wet, in full health, at the beginning of growth, stamped now. Their
credited days are gone with the columns -- a one-off, and the world has a
handful of such beds.

Revision ID: c9e4f2a7b1d3
Revises: a2f9c31b70d4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9e4f2a7b1d3"
down_revision: str | None = "a2f9c31b70d4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "plot", sa.Column("moisture", sa.Numeric(6, 2), nullable=False, server_default="0")
    )
    op.add_column(
        "plot", sa.Column("health", sa.Numeric(6, 2), nullable=False, server_default="100")
    )
    op.add_column(
        "plot", sa.Column("growth", sa.Numeric(6, 2), nullable=False, server_default="0")
    )
    op.add_column("plot", sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "plot",
        sa.Column("growth_boost", sa.Numeric(6, 2), nullable=False, server_default="0"),
    )
    op.add_column("plot", sa.Column("boost_stage", sa.String(), nullable=True))
    op.add_column(
        "plot",
        sa.Column(
            "fed",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "plot", sa.Column("overfed", sa.Integer(), nullable=False, server_default="0")
    )
    for scale in ("moisture", "health", "growth"):
        op.create_check_constraint(
            f"{scale}_in_scale", "plot", f"{scale} >= 0 AND {scale} <= 100"
        )
    #: The beds growing today: half-wet and stamped now, so that the first
    #: read after the deploy finds a life to advance rather than a null.
    op.execute("UPDATE plot SET moisture = 50, settled_at = now() WHERE state = 'sown'")
    op.drop_column("plot", "care_credits")
    op.drop_column("plot", "cared_at")


def downgrade() -> None:
    op.add_column(
        "plot", sa.Column("cared_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "plot", sa.Column("care_credits", sa.Integer(), nullable=False, server_default="0")
    )
    for scale in ("growth", "health", "moisture"):
        #: The bare name: the naming convention adds the `ck_plot_` itself.
        op.drop_constraint(f"{scale}_in_scale", "plot", type_="check")
    op.drop_column("plot", "overfed")
    op.drop_column("plot", "fed")
    op.drop_column("plot", "boost_stage")
    op.drop_column("plot", "growth_boost")
    op.drop_column("plot", "settled_at")
    op.drop_column("plot", "growth")
    op.drop_column("plot", "health")
    op.drop_column("plot", "moisture")
