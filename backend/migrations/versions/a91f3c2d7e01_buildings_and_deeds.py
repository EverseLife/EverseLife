"""buildings and land deeds

A building on a plot (D-106, D-125) and a deed of plot ownership (D-116): a
machine is placed in a building and takes its area, and node ownership is
documented by an electronic document sold by a sale contract.

Revision ID: a91f3c2d7e01
Revises: f7b2c40de915
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a91f3c2d7e01"
down_revision: str | None = "f7b2c40de915"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "building",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("area_m2", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "built_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("area_m2 > 0", name=op.f("ck_building_area_positive")),
        sa.ForeignKeyConstraint(
            ["node_id"], ["node.id"], name=op.f("fk_building_node_id_node")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_building")),
    )
    op.create_index("ix_building_node", "building", ["node_id"], unique=False)

    op.create_table(
        "deed",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("owner_identity_id", sa.Uuid(), nullable=False),
        sa.Column("paid", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("sale_price", sa.BigInteger(), nullable=True),
        sa.Column("sale_to_identity_id", sa.Uuid(), nullable=True),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sale_price IS NULL OR sale_price > 0",
            name=op.f("ck_deed_sale_price_positive"),
        ),
        sa.ForeignKeyConstraint(["node_id"], ["node.id"], name=op.f("fk_deed_node_id_node")),
        sa.ForeignKeyConstraint(
            ["owner_identity_id"],
            ["identity.id"],
            name=op.f("fk_deed_owner_identity_id_identity"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deed")),
        sa.UniqueConstraint("node_id", name=op.f("uq_deed_node_id")),
    )
    op.create_index("ix_deed_owner", "deed", ["owner_identity_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_deed_owner", table_name="deed")
    op.drop_table("deed")
    op.drop_index("ix_building_node", table_name="building")
    op.drop_table("building")
