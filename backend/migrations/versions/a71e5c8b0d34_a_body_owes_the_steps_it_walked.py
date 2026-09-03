# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""a body owes the steps its stamina column could not be charged for

The road costs stamina by time (D-147), and `Body.stamina` keeps hundredths.
At `travel.stamina_per_hour` a step shorter than nine seconds costs less than
half of one, so the charge -- computed, checked against the reserve and
assigned -- was rounded away on the way into the row. The engine believed it
had charged; the row disagreed, and nothing looked.

That is not a ship's problem, though a ship's corridor is one second and its
gangway seven tenths. A city step is four to ten seconds before the paving
takes its share, so most walking inside a city was free. Twenty-two of the
twenty-five paved edges in the seeded world cost nothing at all.

What the column could not be charged for now waits on the body and is paid
with the next step. It is always less than a hundredth, and a check says so:
the width alone would not, `Numeric(9, 9)` holding anything under one.
Existing bodies start at nothing, owing for no road already walked.

Revision ID: a71e5c8b0d34
Revises: f2b6d40a91c3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a71e5c8b0d34"
down_revision: str | None = "f2b6d40a91c3"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "body",
        sa.Column("stamina_owed", sa.Numeric(9, 9), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "stamina_owed_under_a_hundredth",
        "body",
        "stamina_owed >= 0 AND stamina_owed < 0.01",
    )


def downgrade() -> None:
    #: The bare name: the naming convention adds the `ck_body_` itself.
    op.drop_constraint("stamina_owed_under_a_hundredth", "body", type_="check")
    op.drop_column("body", "stamina_owed")
