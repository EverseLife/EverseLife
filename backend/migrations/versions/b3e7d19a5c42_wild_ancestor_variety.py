# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""wild ancestor variety (D-260)

The crop's wild ancestor becomes a distinct cultivar: authorless and stable,
like the base one -- the `wild` flag is what tells the two apart when both
exist. Existing varieties are all tended lines, so the flag backfills false.

Revision ID: b3e7d19a5c42
Revises: e8a1c52f7d94
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3e7d19a5c42"
down_revision: str | None = "e8a1c52f7d94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "variety",
        sa.Column("wild", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    #: The default lives in the model, not the schema: the column arrives with
    #: one only to backfill the rows that predate it.
    op.alter_column("variety", "wild", server_default=None)


def downgrade() -> None:
    op.drop_column("variety", "wild")
