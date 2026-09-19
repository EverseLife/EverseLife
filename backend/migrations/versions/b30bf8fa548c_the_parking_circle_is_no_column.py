"""the parking circle is no column

D-354: a hull above a planet is a body in the sky -- a state the tick moves --
and no longer moored to a planet's orbital node on an analytic circle. The
angle round that circle, `ship.park_phase`, has nothing left to say. Hulls a
world still has moored to an orbital node are put into orbit by the seed's
catch-up (`seed_catchup._orbits_gone`), over the meridian of the pier they
last left: the angle they had is not worth keeping for them.

Revision ID: b30bf8fa548c
Revises: 5797e1bbb4e6
Create Date: 2026-09-19 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b30bf8fa548c'
down_revision: str | None = '5797e1bbb4e6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column('ship', 'park_phase')


def downgrade() -> None:
    op.add_column('ship', sa.Column('park_phase', sa.Float(), nullable=True))
