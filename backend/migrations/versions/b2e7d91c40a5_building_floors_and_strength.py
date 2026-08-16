"""building floors and strength

Storeys and durability tiers were declared by the vault (D-125, D-145) long
before the engine had them: a plot was a hard ceiling on a workshop, because a
house only grew sideways. `footprint_m2` is the ground taken, `area_m2` stays
the usable area -- for existing houses they are the same, one floor of the
first tier.

Revision ID: b2e7d91c40a5
Revises: cc8443a6b882
Create Date: 2026-08-16 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b2e7d91c40a5'
down_revision: str | None = 'cc8443a6b882'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'building',
        sa.Column(
            'footprint_m2', sa.Numeric(precision=12, scale=2),
            nullable=False, server_default='0',
        ),
    )
    op.add_column(
        'building', sa.Column('floors', sa.Integer(), nullable=False, server_default='1')
    )
    op.add_column(
        'building', sa.Column('strength', sa.Integer(), nullable=False, server_default='1')
    )
    #: A house built before storeys stands on exactly its own area: one floor.
    op.execute('UPDATE building SET footprint_m2 = area_m2 WHERE footprint_m2 = 0')
    op.create_check_constraint('footprint_positive', 'building', 'footprint_m2 > 0')
    op.create_check_constraint('floors_positive', 'building', 'floors >= 1')
    op.create_check_constraint('strength_positive', 'building', 'strength >= 1')


def downgrade() -> None:
    op.drop_constraint('strength_positive', 'building', type_='check')
    op.drop_constraint('floors_positive', 'building', type_='check')
    op.drop_constraint('footprint_positive', 'building', type_='check')
    op.drop_column('building', 'strength')
    op.drop_column('building', 'floors')
    op.drop_column('building', 'footprint_m2')
