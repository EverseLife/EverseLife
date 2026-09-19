"""the melt remembers the mark

The mark a coin melt's coins carried -- who minted them, when and where
(D-058) -- kept on the batch. The melt writes its coins off at the start, and
a melt swept away with its job dead gives back exactly what it took (D-217):
before this the coins came back as money nobody minted, and never folded back
into the rest of their stack (D-214). Writes no row: a melt already under way
has lost its mark for good, and should it be swept it now gives its coins back
with its fineness and no quality, but unmarked -- so they still lie beside a
marked stack rather than in it.

Revision ID: a0d258632179
Revises: 5ee5645ab457
Create Date: 2026-09-19 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a0d258632179'
down_revision: str | None = '5ee5645ab457'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('craft_batch', sa.Column('mark_identity_id', sa.Uuid(), nullable=True))
    op.add_column('craft_batch', sa.Column('mark_made_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('craft_batch', sa.Column('mark_node_id', sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column('craft_batch', 'mark_node_id')
    op.drop_column('craft_batch', 'mark_made_at')
    op.drop_column('craft_batch', 'mark_identity_id')
