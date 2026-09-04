"""The hull in the sky

D-289: a ship in space is a state -- a place, a speed and the moment they
were true -- flown by the integrator, and an order the autopilot flies. Six
columns for the state and the parking phase, one for the order, one for the
coast ahead as the tick last counted it, one for the hour the hull was lost.
All nullable: a hull at a spaceport has none of them, and the rows that exist
stand at spaceports or fly the old passage jobs to their end.

A hull moored at an orbital node when this lands gets a stamp and a phase of
nought: without them it is on no circle, the sky offers it nothing, and it
would have to come down and climb again to cross.

Revision ID: 7b2e9d4c1a58
Revises: 3f8a2c6d9b41
Create Date: 2026-09-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7b2e9d4c1a58"
down_revision: str | None = "3f8a2c6d9b41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ship", sa.Column("sky_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ship", sa.Column("sky_x", sa.Float(), nullable=True))
    op.add_column("ship", sa.Column("sky_y", sa.Float(), nullable=True))
    op.add_column("ship", sa.Column("sky_vx", sa.Float(), nullable=True))
    op.add_column("ship", sa.Column("sky_vy", sa.Float(), nullable=True))
    op.add_column("ship", sa.Column("park_phase", sa.Float(), nullable=True))
    op.add_column(
        "ship",
        sa.Column("course", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "ship",
        sa.Column("forecast", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("ship", sa.Column("lost_at", sa.DateTime(timezone=True), nullable=True))
    #: The hulls already hanging over a planet: onto the parking circle, now.
    op.execute(
        "UPDATE ship SET sky_at = now(), park_phase = 0 FROM node "
        "WHERE ship.docked_node_id = node.id AND node.key LIKE '%.orbit' AND ship.sky_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("ship", "lost_at")
    op.drop_column("ship", "forecast")
    op.drop_column("ship", "course")
    op.drop_column("ship", "park_phase")
    op.drop_column("ship", "sky_vy")
    op.drop_column("ship", "sky_vx")
    op.drop_column("ship", "sky_y")
    op.drop_column("ship", "sky_x")
    op.drop_column("ship", "sky_at")
