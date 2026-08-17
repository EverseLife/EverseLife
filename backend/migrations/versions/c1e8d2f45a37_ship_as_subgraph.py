"""the ship: a group of nodes with one connector (D-201, D-202)

The ship's rooms are ordinary nodes and need no table of their own: membership
is the `parent` hierarchy, the same one a city has over its locations. What the
graph cannot say is here -- whose the ship is, which node faces outwards and
which port it is coupled to. An empty `docked_node_id` is the flight.

Revision ID: c1e8d2f45a37
Revises: b4f61c07d2ae
Create Date: 2026-08-17 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c1e8d2f45a37'
down_revision: str | None = 'b4f61c07d2ae'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ship",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("owner_identity_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("connector_node_id", sa.Uuid(), nullable=False),
        sa.Column("docked_node_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["connector_node_id"], ["node.id"], name=op.f("fk_ship_connector_node_id_node")
        ),
        sa.ForeignKeyConstraint(
            ["node_id"], ["node.id"], name=op.f("fk_ship_node_id_node")
        ),
        sa.ForeignKeyConstraint(
            ["owner_identity_id"], ["identity.id"], name=op.f("fk_ship_owner_identity_id_identity")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ship")),
        sa.UniqueConstraint("connector_node_id", name=op.f("uq_ship_connector_node_id")),
        sa.UniqueConstraint("node_id", name=op.f("uq_ship_node_id")),
    )
    op.create_index("ix_ship_docked", "ship", ["docked_node_id"], unique=False)
    op.create_index("ix_ship_owner", "ship", ["owner_identity_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ship_owner", table_name="ship")
    op.drop_index("ix_ship_docked", table_name="ship")
    op.drop_table("ship")
