"""Oxygen aboard and outside

**Oxygen** (D-233, D-234) needs two stamps and no reserves: the air itself is a
liquid and lies in tanks and cylinders, which are ordinary stacks. `body.air_at`
and `ship.air_at` say when breathing was last settled; `body.choking_since` is
the one settling of grace between running out and dying. Existing rows are
stamped **now** rather than at their creation: a body printed a month ago has
not been holding its breath since, and charging it for that month the first
time the tick runs would kill everybody standing on an airless world at once.

A step across a hull becoming one second (D-240) is **not** here: what a step
is worth in seconds is the vault's number, and a migration that wrote 1 into
the table would be a second opinion about it. The seed's catch-up relays those
edges, exactly as it relays the gangways of moored ships.

Revision ID: b3d17f0c8a45
Revises: e4a91c6b30d7
Create Date: 2026-08-28 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3d17f0c8a45"
down_revision: str | None = "e4a91c6b30d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "body",
        sa.Column(
            "air_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.add_column("body", sa.Column("choking_since", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "ship",
        sa.Column(
            "air_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_column("ship", "air_at")
    op.drop_column("body", "choking_since")
    op.drop_column("body", "air_at")
