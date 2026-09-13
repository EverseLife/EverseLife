"""the catch-up remembers its one-off steps

A table of one row per step, and no row written here: `seed_once` writes it,
in the transaction of the step itself. The lines drawn to the hulls of the old
default (D-288 as amended 2026-09-04) ran at every deploy and plumbed back the
ports their owners had emptied; a world that remembers the step has run does
not run it again. Its own table rather than an event, because the journal will
be cut back by month and a mark that went with an old month would run the step
again. A world migrated here runs the step once more at its next seed, and
leaves the ports its owners plumbed alone.

Revision ID: 7171e0c3a511
Revises: e1d7b8231374
Create Date: 2026-09-13 17:44:32.795266
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '7171e0c3a511'
down_revision: str | None = 'e1d7b8231374'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('catch_up_step',
    sa.Column('step', sa.String(), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('step', name=op.f('pk_catch_up_step'))
    )


def downgrade() -> None:
    op.drop_table('catch_up_step')
