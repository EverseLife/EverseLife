"""Chance keeps a memory of its own droughts (D-213)

One row per identity per matter: how many times in a row it has not worked, and
what a deck of choices still holds. Without it a chance is a fair coin, and a
fair coin deals twelve empty runs in a row often enough to cost somebody their
evening.

The table starts empty and fills itself: a missing row means "no drought yet",
which is exactly the state everyone begins in.

Revision ID: f3b7c1d94e02
Revises: e1c8f3a24b70
Create Date: 2026-08-19 18:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f3b7c1d94e02'
down_revision: str | None = 'e1c8f3a24b70'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "luck",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("matter", sa.String(), nullable=False),
        sa.Column("misses", sa.Integer(), nullable=False),
        sa.Column("deck", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["identity.id"], name=op.f("fk_luck_identity_id_identity")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_luck")),
    )
    op.create_index("uq_luck_matter", "luck", ["identity_id", "matter"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_luck_matter", table_name="luck")
    op.drop_table("luck")
