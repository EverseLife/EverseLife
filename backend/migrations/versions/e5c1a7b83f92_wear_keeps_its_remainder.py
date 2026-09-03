# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""wear keeps what the condition column could not show

`Item.condition` holds hundredths, and a single doing may cost less than one:
a rig brought up to date every half minute, a swing of a pick on a fine tool.
That wear was written off against a column that rounded it away, so it never
happened -- and since `rig.advance` and `automat.advance` also moved their
stamp over it, a machine settled oftener than the hundredth takes wore not at
all and D-129 came undone at the tap of a button.

The sliver lives in a column of its own now and is spent on the next write-off,
which mends every stream at once -- the two by the clock and the five by use.
It is always less than a hundredth, and a check says so, since the width
alone would not -- `Numeric(9, 9)` holds anything under one. Existing rows
start at nothing: no thing is owed wear for the doings before
this, and charging them would age the world overnight.

Revision ID: e5c1a7b83f92
Revises: d3f8a29c61b7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e5c1a7b83f92"
down_revision: str | None = "d3f8a29c61b7"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "item",
        sa.Column("wear_remainder", sa.Numeric(9, 9), nullable=False, server_default="0"),
    )
    #: The width alone does not say it: `Numeric(9, 9)` holds anything under
    #: one, and the sliver is always under a hundredth.
    op.create_check_constraint(
        "wear_remainder_under_a_hundredth",
        "item",
        "wear_remainder >= 0 AND wear_remainder < 0.01",
    )


def downgrade() -> None:
    op.drop_constraint("wear_remainder_under_a_hundredth", "item", type_="check")
    op.drop_column("item", "wear_remainder")
