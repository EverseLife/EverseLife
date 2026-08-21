"""building type and condition

The durability tier was one number doing three jobs: it multiplied a single
shared recipe, capped the height and priced the upkeep. From it followed that
a house of any class was built of the same thing, only more of it -- an
eight-storey house of steel and glass spent fourfold timber and rope.

D-218 puts a **type** in its place: a name, and by it the vault gives the
composition per square metre (`build.types`), the price of the next floor
(`build.floor_growth_by_type`) and the rate of decay (`build.decay_by_type`).
The height cap goes away entirely -- the bill refuses more convincingly.

Existing houses are translated by what they were made of: the first tier was
timber, the second stone, the third steel and glass -- the nearest types by
composition are the first, second and fourth of the new ladder. Condition
starts full: nothing decayed before there was decay.

Revision ID: a7d3f1e5c908
Revises: f3b7c1d94e02
Create Date: 2026-08-21 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a7d3f1e5c908'
down_revision: str | None = 'f3b7c1d94e02'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The tier ladder as it was, and the type nearest to each rung by composition.
#: Written out here rather than read from the vault: a migration must give the
#: same result on any future set of constants (01-tech-notes).
TIER_TO_KIND = {1: 'деревянный', 2: 'каменный', 3: 'железобетонный'}


def upgrade() -> None:
    op.add_column(
        'building',
        sa.Column(
            'kind', sa.String(length=64), nullable=False,
            server_default='деревянный',
        ),
    )
    op.add_column(
        'building',
        sa.Column(
            'condition', sa.Numeric(precision=6, scale=2),
            nullable=False, server_default='100',
        ),
    )
    for tier, kind in TIER_TO_KIND.items():
        op.execute(
            sa.text('UPDATE building SET kind = :kind WHERE strength = :tier').bindparams(
                kind=kind, tier=tier
            )
        )
    op.create_check_constraint(
        'building_condition_in_scale', 'building', 'condition >= 0 AND condition <= 100'
    )
    op.drop_constraint('strength_positive', 'building', type_='check')
    op.drop_column('building', 'strength')


def downgrade() -> None:
    op.add_column(
        'building',
        sa.Column('strength', sa.Integer(), nullable=False, server_default='1'),
    )
    for tier, kind in TIER_TO_KIND.items():
        op.execute(
            sa.text('UPDATE building SET strength = :tier WHERE kind = :kind').bindparams(
                kind=kind, tier=tier
            )
        )
    op.create_check_constraint('strength_positive', 'building', 'strength >= 1')
    op.drop_constraint('building_condition_in_scale', 'building', type_='check')
    op.drop_column('building', 'condition')
    op.drop_column('building', 'kind')
