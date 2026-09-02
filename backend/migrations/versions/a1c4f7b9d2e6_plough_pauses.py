# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""the plough pauses with its progress kept (D-277)

A long work is paused, not thrown away: the plot banks the minutes ploughed
and remembers when the current run began.

Revision ID: a1c4f7b9d2e6
Revises: d5c7e1a29f04
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c4f7b9d2e6"
down_revision: str | None = "d5c7e1a29f04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plot",
        sa.Column(
            "plow_done_minutes",
            sa.Numeric(10, 2),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column("plot", sa.Column("plow_since", sa.DateTime(timezone=True), nullable=True))
    #: A plough running at the moment of the upgrade: its run began when
    #: the job was queued, which is the best the journal can say.
    op.execute(
        """
        UPDATE plot SET plow_since = job.created_at
        FROM job
        WHERE plot.state = 'plowing'
          AND job.kind = 'farm.plow'
          AND job.state = 'pending'
          AND job.payload->>'plot' = plot.id::text
        """
    )


def downgrade() -> None:
    op.drop_column("plot", "plow_since")
    op.drop_column("plot", "plow_done_minutes")
