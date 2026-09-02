# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""emission by signatures (D-270)

The capital prints money into its own treasury when the holders of the
`emission` right have signed a proposal: the city learns which one is the
capital, and the proposal and its signatures get their tables.

Revision ID: d5c7e1a29f04
Revises: b8e4d2c7a915
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5c7e1a29f04"
down_revision: str | None = "b8e4d2c7a915"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "city",
        sa.Column("capital", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_table(
        "emission_proposal",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("city_id", sa.Uuid(), nullable=False),
        sa.Column("proposer_identity_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "open",
                "printed",
                "expired",
                name="emission_state",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("printed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["city_id"], ["city.id"], name=op.f("fk_emission_proposal_city_id_city")
        ),
        sa.ForeignKeyConstraint(
            ["proposer_identity_id"],
            ["identity.id"],
            name=op.f("fk_emission_proposal_proposer_identity_id_identity"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_emission_proposal")),
    )
    op.create_index("ix_emission_city", "emission_proposal", ["city_id"], unique=False)
    op.create_index(
        "uq_emission_open_city",
        "emission_proposal",
        ["city_id"],
        unique=True,
        postgresql_where=sa.text("state = 'open'"),
    )
    op.create_table(
        "emission_signature",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["identity.id"],
            name=op.f("fk_emission_signature_identity_id_identity"),
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["emission_proposal.id"],
            name=op.f("fk_emission_signature_proposal_id_emission_proposal"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_emission_signature")),
        sa.UniqueConstraint("proposal_id", "identity_id", name=op.f("uq_emission_signature")),
    )
    #: The emission-share sensor sums what was printed over a window (D-030);
    #: a partial index like the works fund's keeps that off a full scan.
    op.create_index(
        "ix_ledger_transaction_emission",
        "ledger_transaction",
        ["at"],
        unique=False,
        postgresql_where=sa.text("reason = 'emission'"),
    )


def downgrade() -> None:
    op.drop_index("ix_ledger_transaction_emission", table_name="ledger_transaction")
    op.drop_table("emission_signature")
    op.drop_index("uq_emission_open_city", table_name="emission_proposal")
    op.drop_index("ix_emission_city", table_name="emission_proposal")
    op.drop_table("emission_proposal")
    op.drop_column("city", "capital")
