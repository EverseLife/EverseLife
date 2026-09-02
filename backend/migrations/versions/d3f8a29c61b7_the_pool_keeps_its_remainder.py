# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""the pool keeps what it could not store

`EnergyPool.stored` holds thousandths and generation is continuous, so every
pass over a city leaves less than a thousandth over. It used to be dropped and
the stamp moved on regardless, which is invisible on the worker's tick and
ruinous under load: a pool is brought up to date by every command that touches
energy, and a city read often enough generated nothing at all.

The sliver lives in a column of its own now and is spent on the next pass. It
is always less than one thousandth, so the column is too narrow to hold a
whole one -- the bound is the schema's, not just the code's -- and the
existing rows start at nothing: no city is owed anything for the hours before
this, and crediting them would invent energy.

Revision ID: d3f8a29c61b7
Revises: d7f4b1a92c58
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d3f8a29c61b7"
down_revision: str | None = "d7f4b1a92c58"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "energy_pool",
        sa.Column(
            "remainder",
            sa.Numeric(9, 9),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("energy_pool", "remainder")
