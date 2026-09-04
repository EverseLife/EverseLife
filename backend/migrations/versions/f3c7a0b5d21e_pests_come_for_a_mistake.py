# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""pests come for a mistake: four pressures, a trouble and a guard

D-299 gives the bed the fifth care parameter. Four hidden pressures build
from the four mistakes of care (`pest`), the one that crossed the scale is
the trouble that struck (`illness_kind`) with the share of the bed it has
taken (`illness`), and a treatment holds by the class of the thing it was
made with (`guard`: class id -> ISO moment).

Beds growing at the moment of this migration start clean: no pressure, no
trouble, no guard -- the pressures build from the care that follows, and a
bed that is kept well never falls ill at all.

Revision ID: f3c7a0b5d21e
Revises: d4b7e1c2f9a5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3c7a0b5d21e"
down_revision: str | None = "d4b7e1c2f9a5"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "plot",
        sa.Column(
            "pest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "plot", sa.Column("illness", sa.Numeric(6, 2), nullable=False, server_default="0")
    )
    op.add_column("plot", sa.Column("illness_kind", sa.String(), nullable=True))
    op.add_column(
        "plot",
        sa.Column(
            "guard",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_check_constraint("illness_in_scale", "plot", "illness >= 0 AND illness <= 100")


def downgrade() -> None:
    #: The bare name: the naming convention adds the `ck_plot_` itself.
    op.drop_constraint("illness_in_scale", "plot", type_="check")
    op.drop_column("plot", "guard")
    op.drop_column("plot", "illness_kind")
    op.drop_column("plot", "illness")
    op.drop_column("plot", "pest")
