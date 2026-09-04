"""Ships that meet

D-289, wave 3: a hull may come to rest beside another and fly as one with
it (`held_ship_id`), the two may be joined connector to connector once both
commanders agree (`docked_ship_id`, the consent given and not yet returned
in `dock_ask_ship_id`), and a hull keeps which foreign hulls it has in
sight so the journal says "sighted" once (`sightings`). All nullable: a
hull that has met nobody has none of them. The hold and the docking are
indexed for the tick's sweep, the docking partially: marks are few, nulls
are every other hull.

Revision ID: 9c4e7a1f2b63
Revises: 7b2e9d4c1a58
Create Date: 2026-09-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9c4e7a1f2b63"
down_revision: str | None = "7b2e9d4c1a58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ship", sa.Column("held_ship_id", sa.Uuid(), nullable=True))
    op.add_column("ship", sa.Column("docked_ship_id", sa.Uuid(), nullable=True))
    op.add_column("ship", sa.Column("dock_ask_ship_id", sa.Uuid(), nullable=True))
    op.add_column(
        "ship",
        sa.Column("sightings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_ship_held", "ship", ["held_ship_id"])
    #: A Python None went into the JSON columns as the JSON value `null`
    #: until the model said `none_as_null`; the rows written so far are made
    #: to say what they meant, so `IS NULL` reads them.
    for column in ("course", "forecast"):
        op.execute(f"UPDATE ship SET {column} = NULL WHERE {column} = 'null'::jsonb")
    op.create_index(
        "ix_ship_docked_ship",
        "ship",
        ["docked_ship_id"],
        postgresql_where=sa.text("docked_ship_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_ship_docked_ship", table_name="ship")
    op.drop_index("ix_ship_held", table_name="ship")
    op.drop_column("ship", "sightings")
    op.drop_column("ship", "dock_ask_ship_id")
    op.drop_column("ship", "docked_ship_id")
    op.drop_column("ship", "held_ship_id")
