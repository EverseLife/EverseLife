"""city administration, utility meters, busy stations

Город как институт (D-154), счётчик быта (D-149) и занятость станка (D-150).
Миграция написана руками, а не автогенерацией: в дереве моделей одновременно
шла другая работа, и `--autogenerate` сгрёб бы чужие незамигрированные таблицы
в этот файл.

Revision ID: d4c1a90f2b73
Revises: c31a7f0e5b42
Create Date: 2026-08-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4c1a90f2b73"
down_revision: str | None = "c31a7f0e5b42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "city",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("founder_identity_id", sa.Uuid(), nullable=True),
        sa.Column("charter", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "charter_params", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("laws", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["founder_identity_id"],
            ["identity.id"],
            name=op.f("fk_city_founder_identity_id_identity"),
        ),
        sa.ForeignKeyConstraint(["node_id"], ["node.id"], name=op.f("fk_city_node_id_node")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_city")),
        sa.UniqueConstraint("node_id", name=op.f("uq_city_node_id")),
    )

    op.create_table(
        "city_office",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("city_id", sa.Uuid(), nullable=False),
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("powers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("appointed_by_identity_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["appointed_by_identity_id"],
            ["identity.id"],
            name=op.f("fk_city_office_appointed_by_identity_id_identity"),
        ),
        sa.ForeignKeyConstraint(
            ["city_id"], ["city.id"], name=op.f("fk_city_office_city_id_city")
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["identity.id"], name=op.f("fk_city_office_identity_id_identity")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_city_office")),
    )
    op.create_index("ix_city_office_city", "city_office", ["city_id"], unique=False)
    op.create_index("ix_city_office_identity", "city_office", ["identity_id"], unique=False)

    op.create_table(
        "city_grant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("city_id", sa.Uuid(), nullable=False),
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["city_id"], ["city.id"], name=op.f("fk_city_grant_city_id_city")
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["identity.id"], name=op.f("fk_city_grant_identity_id_identity")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_city_grant")),
        sa.UniqueConstraint("city_id", "identity_id", name="uq_city_grant_identity"),
    )

    op.create_table(
        "utility_meter",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column(
            "counted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("debt", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "cut_off", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "last_energy",
            sa.Numeric(precision=12, scale=3),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["node_id"], ["node.id"], name=op.f("fk_utility_meter_node_id_node")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_utility_meter")),
        sa.UniqueConstraint("node_id", name=op.f("uq_utility_meter_node_id")),
    )

    #: Занятость станка (D-150): за станком работает один, и это свойство
    #: самого станка, а не отдельная таблица очередей.
    op.add_column("item", sa.Column("busy_body_id", sa.Uuid(), nullable=True))
    op.add_column(
        "item", sa.Column("busy_until", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("item", "busy_until")
    op.drop_column("item", "busy_body_id")
    op.drop_table("utility_meter")
    op.drop_table("city_grant")
    op.drop_index("ix_city_office_identity", table_name="city_office")
    op.drop_index("ix_city_office_city", table_name="city_office")
    op.drop_table("city_office")
    op.drop_table("city")
