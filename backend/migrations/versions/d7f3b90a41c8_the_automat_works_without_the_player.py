"""The automat works without the player

The second production order arrives (D-253, revising D-035): an automat
standing in a building executes the recipe its owner programmed, slower than
a hand and never above its quality ceiling, for as long as the building's
vessels hold lubricant, the city pool holds energy and the yard holds
inputs. One table, shaped like the rig's: the machine item, the node it
stands in, the owner the energy bill goes to, the programmed recipe and the
backlog -- work done but not yet paid out, because a piece crosses tick
boundaries and a liquid waits for room in a vessel.

Revision ID: d7f3b90a41c8
Revises: c9e4a71b2d06
Create Date: 2026-09-01 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7f3b90a41c8"
down_revision: str | None = "c9e4a71b2d06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "automat",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("owner_identity_id", sa.Uuid(), nullable=True),
        sa.Column("recipe_key", sa.String(), nullable=True),
        sa.Column("backlog", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("counted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["node.id"]),
        sa.ForeignKeyConstraint(["owner_identity_id"], ["identity.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id"),
    )
    op.create_index("ix_automat_node", "automat", ["node_id"])


def downgrade() -> None:
    op.drop_index("ix_automat_node", table_name="automat")
    op.drop_table("automat")
