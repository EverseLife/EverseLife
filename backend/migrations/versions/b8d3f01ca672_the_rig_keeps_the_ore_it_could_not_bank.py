# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""the rig keeps the ore its hopper could not be credited with

`Rig.hopper` holds thousandths and the machine is settled by elapsed time, so
a short pass raises less than one. The hopper was credited by writing the sum
straight to the column, which rounded it away -- while the vein had already
been emptied for that same ore, and by twice as much again
(`rig.depletion_multiplier`). The ore left the world and reached nobody.

The sliver lives in a column of its own now and is credited on the next pass,
and the vein is emptied for what was actually raised. It cannot ride on
`counted_at`: that stamp measures the mining, the fuel and the wear together,
and holding it back would raise the same ore a second time -- which is exactly
what a race on the hopper caught when this family was fixed for the wear.

It is always less than a thousandth, and a check says so: the width alone
would not, `Numeric(9, 9)` holding anything under one. Existing rigs start at
nothing, owed no ore for the passes before this.

Revision ID: b8d3f01ca672
Revises: a71e5c8b0d34
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b8d3f01ca672"
down_revision: str | None = "a71e5c8b0d34"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "rig",
        sa.Column("hopper_remainder", sa.Numeric(9, 9), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "hopper_remainder_under_a_thousandth",
        "rig",
        "hopper_remainder >= 0 AND hopper_remainder < 0.001",
    )
    #: And the coal owed for it. A thousandth of ore costs less coal than the
    #: fuel column can show, so charging by the clock while keeping the ore
    #: would have raised ore for nothing at all.
    op.add_column(
        "rig",
        sa.Column("fuel_remainder", sa.Numeric(9, 9), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "fuel_remainder_under_a_thousandth",
        "rig",
        "fuel_remainder >= 0 AND fuel_remainder < 0.001",
    )


def downgrade() -> None:
    #: The bare name: the naming convention adds the `ck_rig_` itself.
    op.drop_constraint("fuel_remainder_under_a_thousandth", "rig", type_="check")
    op.drop_column("rig", "fuel_remainder")
    op.drop_constraint("hopper_remainder_under_a_thousandth", "rig", type_="check")
    op.drop_column("rig", "hopper_remainder")
