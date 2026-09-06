"""A map sheet holds its places (D-319 item 6, OQ-145).

Revision ID: d5f1a8c2e937
Revises: c3e9b2a7d415
Create Date: 2026-09-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5f1a8c2e937"
down_revision: str | None = "c3e9b2a7d415"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "map_sheet",
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("drawn_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("places", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("item_id"),
    )


def downgrade() -> None:
    op.drop_table("map_sheet")
