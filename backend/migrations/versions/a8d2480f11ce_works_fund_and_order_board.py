"""The works fund and the state order board (D-248).

The fund itself is a ledger account of a new kind and needs no table; the
enum columns are VARCHAR without a CHECK, so the new account kind and posting
reasons need no DDL either. What is new is the order board.

Revision ID: a8d2480f11ce
Revises: e2c74b105f83
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = 'a8d2480f11ce'
down_revision: str | None = 'e2c74b105f83'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'work_order',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column(
            'kind',
            sa.Enum(
                'road_mend',
                'building_repair',
                'building_build',
                'fuel_delivery',
                name='work_order_kind',
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            'state',
            sa.Enum(
                'open',
                'done',
                'cancelled',
                name='work_order_state',
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column('edge_id', sa.Uuid(), nullable=True),
        sa.Column('node_id', sa.Uuid(), nullable=True),
        sa.Column('city_id', sa.Uuid(), nullable=True),
        sa.Column('payload', JSONB(), nullable=False),
        sa.Column('tariff', sa.BigInteger(), nullable=False),
        sa.Column('done_by', sa.Uuid(), nullable=True),
        sa.Column(
            'posted_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['edge_id'], ['edge.id'], name=op.f('fk_work_order_edge_id_edge')),
        sa.ForeignKeyConstraint(['node_id'], ['node.id'], name=op.f('fk_work_order_node_id_node')),
        sa.ForeignKeyConstraint(['city_id'], ['city.id'], name=op.f('fk_work_order_city_id_city')),
        sa.ForeignKeyConstraint(
            ['done_by'], ['identity.id'], name=op.f('fk_work_order_done_by_identity')
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_work_order')),
    )
    op.create_index('ix_work_order_state', 'work_order', ['kind', 'state'], unique=False)
    op.create_index('ix_work_order_edge', 'work_order', ['edge_id'], unique=False)
    op.create_index(
        'uq_work_order_open_edge',
        'work_order',
        ['kind', 'edge_id'],
        unique=True,
        postgresql_where=sa.text("state = 'open'"),
    )
    op.create_index(
        'uq_work_order_open_node',
        'work_order',
        ['kind', 'node_id'],
        unique=True,
        postgresql_where=sa.text("state = 'open'"),
    )
    op.create_index(
        'ix_ledger_transaction_works_print',
        'ledger_transaction',
        ['at'],
        unique=False,
        postgresql_where=sa.text("reason = 'works_print'"),
    )
    #: A treasury loan (D-248, wave 3) has no identity: the borrower is the city.
    op.alter_column('loan', 'identity_id', existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    op.alter_column('loan', 'identity_id', existing_type=sa.Uuid(), nullable=False)
    #: IF EXISTS: the revision was amended with these two indexes while dev
    #: databases already carried its first cut, and a downgrade must pass on both.
    op.execute('DROP INDEX IF EXISTS ix_ledger_transaction_works_print')
    op.execute('DROP INDEX IF EXISTS uq_work_order_open_node')
    op.execute('DROP INDEX IF EXISTS uq_work_order_open_edge')
    op.drop_index('ix_work_order_edge', table_name='work_order')
    op.drop_index('ix_work_order_state', table_name='work_order')
    op.drop_table('work_order')
