"""the hull plumbed for air

Wave 4 of D-288, written down as D-340. Three things, and none of them writes
a row: a name the owner may give an installed vessel (`vessel_name`, a table
of its own so the stack paths of `item` never learn of it); the reason an
automat on the hull's lines stands, kept so the crew is told once (`automat.
stall`); and the thousandths of oxygen the hydroponic beds breathed and a
stack could not yet hold (`ship.air_grown`, with the check that keeps it
under one).

Revision ID: 4fa065ef3486
Revises: c4a239144afd
Create Date: 2026-09-13 15:08:16.026658
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '4fa065ef3486'
down_revision: str | None = 'c4a239144afd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('vessel_name',
    sa.Column('vessel_item_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['vessel_item_id'], ['item.id'], name=op.f('fk_vessel_name_vessel_item_id_item'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('vessel_item_id', name=op.f('pk_vessel_name'))
    )
    op.add_column('automat', sa.Column('stall', sa.String(), nullable=True))
    op.add_column('ship', sa.Column('air_grown', sa.Numeric(precision=9, scale=9), server_default='0', nullable=False))
    op.create_check_constraint(op.f('ck_ship_air_grown_under_a_thousandth'), 'ship', 'air_grown >= 0 AND air_grown < 0.001')


def downgrade() -> None:
    op.drop_constraint(op.f('ck_ship_air_grown_under_a_thousandth'), 'ship', type_='check')
    op.drop_column('ship', 'air_grown')
    op.drop_column('automat', 'stall')
    op.drop_table('vessel_name')
