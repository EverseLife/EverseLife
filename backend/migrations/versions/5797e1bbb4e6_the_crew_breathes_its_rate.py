"""the crew breathes its rate

The breath of a crew aboard that a thousandth could not yet hold
(`ship.air_owed`, with the check that keeps it under one). A stretch is a
minute, and rounding each minute's draw to the nearest thousandth made a crew
of one breathe a fifth more than `oxygen.crew_draw` says (D-234, D-288); the
sliver is carried to the next stretch instead, as `body.air_owed` carries it
outside. Writes no row: every hull starts owing nothing.

Revision ID: 5797e1bbb4e6
Revises: b129894d1b9a
Create Date: 2026-09-13 18:40:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '5797e1bbb4e6'
down_revision: str | None = 'b129894d1b9a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('ship', sa.Column('air_owed', sa.Numeric(precision=9, scale=9), server_default='0', nullable=False))
    op.create_check_constraint(op.f('ck_ship_air_owed_under_a_thousandth'), 'ship', 'air_owed >= 0 AND air_owed < 0.001')


def downgrade() -> None:
    op.drop_constraint(op.f('ck_ship_air_owed_under_a_thousandth'), 'ship', type_='check')
    op.drop_column('ship', 'air_owed')
