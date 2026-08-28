"""The pier a hull cast off from

"Turn back" has to point somewhere (D-242), and undocking is the moment that
erases the other end: a passage under way carries only where it is going. So
the port is remembered on the hull at the casting off.

Empty for every ship that exists today, and that is correct rather than a gap:
none of them is under way with a remembered origin, and the one already in
flight simply cannot be turned back -- it arrives, and every casting off after
this writes the column.

Revision ID: c9f4a1e07b52
Revises: b3d17f0c8a45
Create Date: 2026-08-28 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9f4a1e07b52"
down_revision: str | None = "b3d17f0c8a45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ship", sa.Column("left_node_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("ship", "left_node_id")
