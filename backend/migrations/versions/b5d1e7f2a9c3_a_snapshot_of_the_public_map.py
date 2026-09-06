"""a snapshot of the public map

D-319 п. 7: `/public/map` without a token serves the public surface as it
was `map.public_delay_days` ago. The daily tick writes the rows of the map
as one record, and the route serves the newest record old enough; the
memory of places (п. 6) needs no table of its own -- it is `knowledge` of
kind `place`, and the kind column is a varchar.

Revision ID: b5d1e7f2a9c3
Revises: 3e7c1a9d5b42
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b5d1e7f2a9c3"
down_revision: str | None = "3e7c1a9d5b42"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "map_snapshot",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "taken_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_map_snapshot_taken", "map_snapshot", ["taken_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_map_snapshot_taken", table_name="map_snapshot")
    op.drop_table("map_snapshot")
