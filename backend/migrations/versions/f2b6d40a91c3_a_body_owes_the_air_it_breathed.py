# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""a body owes the air it breathed but could not be charged for

Air is split into thousandths and a step on an airless world can cost less
than one: the gangway off a landed ship is seven tenths of a second, and every
step settles the breathing. Asked for and rounded away, that breath was free,
and the stamp moved over it -- a suited body could stand on Pyroxis for ever
on a single drop.

The debt cannot ride on `air_at`. A stretch may end aboard, and arriving in
air moves the stamp to now, which would forgive whatever the ground had not
yet paid -- the very cycle the gangway makes. It lives on the body instead,
survives the change of place, and is asked for on the next stretch outside.

It is always less than a thousandth, and a check says so: the width alone
would not, `Numeric(9, 9)` holding anything under one. Existing bodies start
at nothing, owing no air for the breaths before this.

Revision ID: f2b6d40a91c3
Revises: e5c1a7b83f92
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f2b6d40a91c3"
down_revision: str | None = "e5c1a7b83f92"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "body",
        sa.Column("air_owed", sa.Numeric(9, 9), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "air_owed_under_a_thousandth",
        "body",
        "air_owed >= 0 AND air_owed < 0.001",
    )


def downgrade() -> None:
    #: The bare name: the naming convention adds the `ck_body_` itself.
    op.drop_constraint("air_owed_under_a_thousandth", "body", type_="check")
    op.drop_column("body", "air_owed")
