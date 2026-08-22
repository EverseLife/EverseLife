"""distance to the city centre is written down

Land is dearer to buy and to hold near the bioprinter, and how much dearer is
decided by the distance in nodes (D-220). That distance was walked for on every
question: read the whole edge table, walk the graph, throw the result away. The
day's land tax asked it once per held plot, so a city of a thousand plots meant
a thousand walks of the same graph; the plot screen asked twice for one node.

So it is written on the node instead, together with the centre it was measured
to. Nothing is filled in here: the first reader of each city measures it and
writes it down for the whole city at once. That keeps the migration honest --
it would otherwise have to know which printer is a door, and that is a rule of
the engine (`world.is_door`), not of the schema.

The number stays true by two things: the centre it was measured to is compared
with the city's centre at every read, and the whole column is emptied wherever
an edge appears or goes (`travel.connect`).

Revision ID: c2f5a90b7e41
Revises: a7d3f1e5c908
Create Date: 2026-08-22 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c2f5a90b7e41'
down_revision: str | None = 'a7d3f1e5c908'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    #: No foreign key to `node` on purpose: this is a measurement, not a tie.
    #: A printer carried out of the core must not be held back by a thousand
    #: plots pointing at the place it stood -- they simply stop matching and
    #: are measured again.
    op.add_column('node', sa.Column('center_node_id', sa.Uuid(), nullable=True))
    op.add_column('node', sa.Column('center_steps', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('node', 'center_steps')
    op.drop_column('node', 'center_node_id')
