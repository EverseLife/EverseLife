"""node gate and roster

The gate of one's own yard (D-199): a plot had a holder, yet anyone could walk
in. `gated` is the gate itself, `node_pass` is the single roster whose meaning
the gate turns over -- blacklist while open, whitelist while shut.

Revision ID: c9f4a2b81d30
Revises: b2e7d91c40a5
Create Date: 2026-08-16 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c9f4a2b81d30'
down_revision: str | None = 'b2e7d91c40a5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'node',
        sa.Column('gated', sa.Boolean(), nullable=False, server_default='false'),
    )
    op.create_table(
        'node_pass',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('node_id', sa.Uuid(), nullable=False),
        sa.Column('identity_id', sa.Uuid(), nullable=False),
        sa.Column(
            'listed_at', sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text('now()'),
        ),
        sa.ForeignKeyConstraint(['node_id'], ['node.id']),
        sa.ForeignKeyConstraint(['identity_id'], ['identity.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('node_id', 'identity_id', name='uq_node_pass'),
    )
    op.create_index('ix_node_pass_node', 'node_pass', ['node_id'])


def downgrade() -> None:
    op.drop_index('ix_node_pass_node', table_name='node_pass')
    op.drop_table('node_pass')
    op.drop_column('node', 'gated')
