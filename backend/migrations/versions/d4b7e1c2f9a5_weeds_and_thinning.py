# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""weeds come up with the crop, and a stand is thinned once

Wave 2 of D-296 (D-297): a sown bed carries its weeds as of `settled_at` --
up from sowing, faster on rich land, drinking beside the crop and dragging
its growth, cleared by a weeding -- and whether this sowing was thinned:
once, early, at its own cost. Existing beds start clean and unthinned.

Revision ID: d4b7e1c2f9a5
Revises: c9e4f2a7b1d3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d4b7e1c2f9a5"
down_revision: str | None = "c9e4f2a7b1d3"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "plot", sa.Column("weeds", sa.Numeric(6, 2), nullable=False, server_default="0")
    )
    op.create_check_constraint("weeds_in_scale", "plot", "weeds >= 0 AND weeds <= 100")
    op.add_column(
        "plot",
        sa.Column("thinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("plot", "thinned")
    #: The bare name: the naming convention adds the `ck_plot_` itself.
    op.drop_constraint("weeds_in_scale", "plot", type_="check")
    op.drop_column("plot", "weeds")
