"""Invention, knowledge carriers, library contents, batches that wait (D-209)

Three things at once, all from one decision:

* an item may carry a recipe (`item.recipe_key`), and so may the batch that
  writes it;
* a batch no longer only runs or is done -- it may **wait**: behind another
  work of the same body, or frozen while the master is away. What is left to
  do lives in `remaining_seconds`, the run counter guards a job left over from
  a frozen run, and `ready_at` may be empty while nothing moves;
* a library holds what was put into it (`library_entry`), not the whole
  catalog. The capital's base set is laid down by the seed's catch-up, not
  here: which recipes are "base" is the vault's business.

Revision ID: b3d7e0a15c92
Revises: f1b8c92d47a3
Create Date: 2026-08-18 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b3d7e0a15c92'
down_revision: str | None = 'f1b8c92d47a3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("item", sa.Column("recipe_key", sa.String(), nullable=True))

    op.add_column("craft_batch", sa.Column("recipe_key", sa.String(), nullable=True))
    op.add_column("craft_batch", sa.Column("station", sa.String(), nullable=True))
    op.add_column(
        "craft_batch",
        sa.Column("remaining_seconds", sa.Numeric(precision=12, scale=3), nullable=True),
    )
    op.add_column(
        "craft_batch",
        sa.Column("run_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "craft_batch",
        sa.Column("runs", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("craft_batch", "ready_at", existing_type=sa.DateTime(timezone=True),
                    nullable=True)
    #: A batch already under way keeps its bar: the run began when it started.
    op.execute("UPDATE craft_batch SET run_started_at = started_at WHERE state = 'running'")
    #: The machine's name comes from the item it occupies.
    op.execute(
        "UPDATE craft_batch SET station = item.type_key FROM item "
        "WHERE item.id = craft_batch.station_item_id"
    )

    op.create_table(
        "library_entry",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("recipe", sa.String(), nullable=False),
        sa.Column("contributor_identity_id", sa.Uuid(), nullable=True),
        sa.Column(
            "contributed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["node_id"], ["node.id"], name=op.f("fk_library_entry_node_id_node")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_library_entry")),
        sa.UniqueConstraint("node_id", "recipe", name=op.f("uq_library_entry_node_recipe")),
    )
    op.create_index("ix_library_entry_node", "library_entry", ["node_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_library_entry_node", table_name="library_entry")
    op.drop_table("library_entry")
    op.alter_column("craft_batch", "ready_at", existing_type=sa.DateTime(timezone=True),
                    nullable=False)
    op.drop_column("craft_batch", "runs")
    op.drop_column("craft_batch", "run_started_at")
    op.drop_column("craft_batch", "remaining_seconds")
    op.drop_column("craft_batch", "station")
    op.drop_column("craft_batch", "recipe_key")
    op.drop_column("item", "recipe_key")
