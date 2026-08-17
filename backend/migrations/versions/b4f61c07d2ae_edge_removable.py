"""an edge can be removed: undocking a ship (D-201)

Until now the graph only grew, and `travel.edge_id` could safely be a hard
reference: no edge ever disappeared. A ship is a subgraph that couples to a
spaceport by one edge, and undocking removes that edge -- while the journal of
transits already walked over it stays. So the reference becomes nullable and
lets go by itself: from, to and the times are the record of a past leg, the
edge is only a pointer.

A leg **under way** never loses its edge: `travel.disconnect` refuses to remove
an edge somebody is walking.

Revision ID: b4f61c07d2ae
Revises: d7a3e5c19b84
Create Date: 2026-08-17 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b4f61c07d2ae'
down_revision: str | None = 'd7a3e5c19b84'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "fk_travel_edge_id_edge"


def upgrade() -> None:
    op.alter_column("travel", "edge_id", existing_type=sa.Uuid(), nullable=True)
    op.drop_constraint(CONSTRAINT, "travel", type_="foreignkey")
    op.create_foreign_key(
        CONSTRAINT, "travel", "edge", ["edge_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    #: Legs whose edge is gone cannot be restored -- there is nothing to point
    #: them at. They are dropped: they are history of walking, not property.
    op.execute("DELETE FROM travel WHERE edge_id IS NULL")
    op.drop_constraint(CONSTRAINT, "travel", type_="foreignkey")
    op.create_foreign_key(CONSTRAINT, "travel", "edge", ["edge_id"], ["id"])
    op.alter_column("travel", "edge_id", existing_type=sa.Uuid(), nullable=False)
