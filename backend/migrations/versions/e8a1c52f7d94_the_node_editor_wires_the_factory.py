"""The node editor wires the factory

Wave 5 of D-253: a wire between two automats of one node. Its mechanical
meaning is the tick's order -- a producer advances before the consumer it
feeds, so a chain flows within one pass instead of lagging a tick per
stage; the rest is the picture the editor draws. Keyed by the machine items
themselves: a wire may be drawn before either end is programmed, and it
must not conjure a working row. A dismantled or worn-out machine takes its
wires along (CASCADE).

Revision ID: e8a1c52f7d94
Revises: d7f3b90a41c8
Create Date: 2026-09-01 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8a1c52f7d94"
down_revision: str | None = "d7f3b90a41c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "automat_link",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("from_item_id", sa.Uuid(), nullable=False),
        sa.Column("to_item_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("from_item_id <> to_item_id", name="automat_link_not_a_loop"),
        sa.ForeignKeyConstraint(["from_item_id"], ["item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_item_id"], ["item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("from_item_id", "to_item_id", name="uq_automat_link"),
    )
    op.create_index("ix_automat_link_from", "automat_link", ["from_item_id"])
    op.create_index("ix_automat_link_to", "automat_link", ["to_item_id"])


def downgrade() -> None:
    op.drop_index("ix_automat_link_to", table_name="automat_link")
    op.drop_index("ix_automat_link_from", table_name="automat_link")
    op.drop_table("automat_link")
