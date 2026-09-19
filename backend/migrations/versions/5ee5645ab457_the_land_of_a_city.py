"""the land of a city

An index on whose city a node is (D-356). The line of every city is asked
every tick: its frame and the covered plots it may let go are read by the
city a node belongs to, and so is every walk over a city's land
(`estate.measure_cities`, `city.lookup.territory`) -- without it each one read
the whole table of nodes. Writes no row.

Revision ID: 5ee5645ab457
Revises: df2137f68672
Create Date: 2026-09-19 15:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '5ee5645ab457'
down_revision: str | None = 'df2137f68672'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index('ix_node_owner_city', 'node', ['owner_city_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_node_owner_city', table_name='node')
