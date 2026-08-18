"""Foraging on empty land (D-210)

One row per body: the search under way, or the find waiting for a decision.
What will be found is decided at the start and revealed by the deadline, so
the row carries the thing, its handful and its quality from the first moment.

Revision ID: c9d4e2a7b1f3
Revises: b3d7e0a15c92
Create Date: 2026-08-18 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c9d4e2a7b1f3'
down_revision: str | None = 'b3d7e0a15c92'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "forage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("body_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("found", sa.String(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("quality", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.ForeignKeyConstraint(["body_id"], ["body.id"], name=op.f("fk_forage_body_id_body")),
        sa.ForeignKeyConstraint(["node_id"], ["node.id"], name=op.f("fk_forage_node_id_node")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_forage")),
    )
    op.create_index("uq_forage_body", "forage", ["body_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_forage_body", table_name="forage")
    op.drop_table("forage")
