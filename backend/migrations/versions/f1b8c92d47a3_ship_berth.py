"""A docked ship holds a numbered berth

The gangway is as long as the berth's number (D-201): the first ship in is a
second's walk from the yard, the fifth is five. The number has to be kept --
otherwise two ships would be handed the same place, and the walk would change
under a player every time somebody else cast off.

Ships already standing at a pier are numbered by the order they docked in. The
gangway edges they already have keep whatever length they were laid with; the
seed's catch-up relays them, because how many seconds a berth is worth is the
vault's number and not this file's business.

Revision ID: f1b8c92d47a3
Revises: e4c7b1a90f52
Create Date: 2026-08-18 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f1b8c92d47a3'
down_revision: str | None = 'e4c7b1a90f52'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ship", sa.Column("berth", sa.Integer(), nullable=True))
    op.execute(
        """
        WITH numbered AS (
            SELECT id, row_number() OVER (
                PARTITION BY docked_node_id ORDER BY created_at
            ) AS place
            FROM ship
            WHERE docked_node_id IS NOT NULL
        )
        UPDATE ship SET berth = numbered.place
        FROM numbered WHERE ship.id = numbered.id
        """
    )


def downgrade() -> None:
    op.drop_column("ship", "berth")
