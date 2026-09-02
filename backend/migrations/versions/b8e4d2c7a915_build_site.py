# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""build site (D-266)

A house used to be bought in one motion out of the builder's hands. The
site takes the materials by parts -- the bill and what was brought, goods
key to units -- and walks three phases: gathering, building, ready, done.

Revision ID: b8e4d2c7a915
Revises: 742125dad53f
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8e4d2c7a915"
down_revision: str | None = "742125dad53f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "build_site",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("owner_identity_id", sa.Uuid(), nullable=False),
        sa.Column("footprint_m2", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("floors", sa.Integer(), server_default="1", nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("needed", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("brought", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "gathering",
                "building",
                "ready",
                "done",
                name="build_site_state",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "laid_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("footprint_m2 > 0", name=op.f("ck_build_site_site_footprint_positive")),
        sa.CheckConstraint("floors >= 1", name=op.f("ck_build_site_site_floors_positive")),
        sa.ForeignKeyConstraint(["node_id"], ["node.id"], name=op.f("fk_build_site_node_id_node")),
        sa.ForeignKeyConstraint(
            ["owner_identity_id"],
            ["identity.id"],
            name=op.f("fk_build_site_owner_identity_id_identity"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_build_site")),
    )
    op.create_index("ix_build_site_node", "build_site", ["node_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_build_site_node", table_name="build_site")
    op.drop_table("build_site")
