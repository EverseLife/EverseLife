"""The surface by degrees: an index on a node's latitude and longitude.

An aim of the scout (D-321) reads the nodes within a window round its point;
without an index every aim read the whole surface of the planet.

Revision ID: c3e9b2a7d415
Revises: b5d1e7f2a9c3
Create Date: 2026-09-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c3e9b2a7d415"
down_revision: str | None = "b5d1e7f2a9c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_node_map_lat", "node", [sa.text("((((properties -> 'map'::text) ->> 'lat'::text))::double precision)")])
    op.create_index("ix_node_map_lon", "node", [sa.text("((((properties -> 'map'::text) ->> 'lon'::text))::double precision)")])


def downgrade() -> None:
    op.drop_index("ix_node_map_lon", table_name="node")
    op.drop_index("ix_node_map_lat", table_name="node")
