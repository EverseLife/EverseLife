"""the batches of a place

An index on the unfinished work of a node (D-351). Whether a machine may be
taken down asks which batches of this node still need a machine of its name,
and a master who wakes or arrives takes up the batches of the node he is in;
both read by the node, and through `ix_craft_batch_ready` (state first) they
scanned every unfinished batch in the world. Writes no row.

Revision ID: df2137f68672
Revises: 5797e1bbb4e6
Create Date: 2026-09-18 21:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'df2137f68672'
down_revision: str | None = '5797e1bbb4e6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index('ix_craft_batch_node', 'craft_batch', ['node_id', 'state'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_craft_batch_node', table_name='craft_batch')
