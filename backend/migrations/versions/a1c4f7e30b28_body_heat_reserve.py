"""The body has a heat reserve

Aurora is permafrost and Pyroxis is a furnace (D-231): the node is either warm
or it is not, and the body carries hours of reserve rather than a temperature.
Two columns say it -- how much was left and when that was true -- so a body
standing in a warm node costs the world no writes at all.

The reserve is **nullable on purpose**: empty means never measured, and that is
exactly what every body printed before this migration is. A zero backfill would
have meant "frozen from birth" for all of them, and a number backfill would have
put a balance value from the vault into a migration (D-065).

Revision ID: a1c4f7e30b28
Revises: f7a3c2e91b04
Create Date: 2026-08-25 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a1c4f7e30b28'
down_revision: str | None = 'f7a3c2e91b04'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("body", sa.Column("warmth", sa.Numeric(6, 2), nullable=True))
    op.add_column(
        "body",
        sa.Column(
            "warmth_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_column("body", "warmth_at")
    op.drop_column("body", "warmth")
