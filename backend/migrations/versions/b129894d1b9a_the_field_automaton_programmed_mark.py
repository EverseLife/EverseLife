"""the field automaton programmed mark

The automat family's minute reads `field_automat` three times (D-339): the
machines to walk, their demand, and the stopped rows to sweep -- each filtered
on whether the programme is empty. Filtered on `jsonb_array_length(program)`,
the planner misjudged them all, an expression having no statistics, and the
sweep read the whole table for its few rows. The mark is a stored generated
column, so nothing writes it but the database, and a partial index lies on the
stopped machines. The programmed ones get none: the minute reads and rewrites
nearly all of them. No row changes meaning; adding the column rewrites the
table once.

Revision ID: b129894d1b9a
Revises: 4fa065ef3486
Create Date: 2026-09-13 23:20:39.235730
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b129894d1b9a'
down_revision: str | None = '4fa065ef3486'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('field_automat', sa.Column('programmed', sa.Boolean(), sa.Computed('jsonb_array_length(program) > 0', persisted=True), nullable=False))
    op.create_index('ix_field_automat_stopped', 'field_automat', ['item_id'], unique=False, postgresql_where=sa.text('NOT programmed'))


def downgrade() -> None:
    op.drop_index('ix_field_automat_stopped', table_name='field_automat', postgresql_where=sa.text('NOT programmed'))
    op.drop_column('field_automat', 'programmed')
