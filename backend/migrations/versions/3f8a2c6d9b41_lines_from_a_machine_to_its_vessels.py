"""Lines from a machine to its vessels

D-288: a machine aboard drinks from and pours into the vessels on its lines,
across every compartment of the hull, and a port with no line takes any
suitable vessel. One table, keyed by the two items and ordered by rank; the
rows go with either item (CASCADE).

Revision ID: 3f8a2c6d9b41
Revises: a71e5c8b0d34
Create Date: 2026-09-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f8a2c6d9b41"
down_revision: str | None = "b8d3f01ca672"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feed_line",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("machine_item_id", sa.Uuid(), nullable=False),
        sa.Column("port", sa.String(), nullable=False),
        sa.Column("vessel_item_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["machine_item_id"], ["item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vessel_item_id"], ["item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("machine_item_id", "port", "vessel_item_id", name="uq_feed_line"),
    )
    op.create_index("ix_feed_line_machine", "feed_line", ["machine_item_id", "port"])
    op.create_index("ix_feed_line_vessel", "feed_line", ["vessel_item_id"])


def downgrade() -> None:
    op.drop_index("ix_feed_line_vessel", table_name="feed_line")
    op.drop_index("ix_feed_line_machine", table_name="feed_line")
    op.drop_table("feed_line")
