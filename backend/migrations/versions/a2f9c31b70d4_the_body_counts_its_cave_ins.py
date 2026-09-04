# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""the body counts the cave-ins it has lived through

The roof used to kill by a coin -- `mine.collapse_death_chance` -- and a coin
can be waited out. It is a count now (D-294): the first cave-in spares the
body, the second takes it. The count sits on the body and not on the identity,
so a newly printed body meets the roof with its grace back and no timer has to
forgive anybody.

Bodies that were already walking around start at nought: the cave-ins they
lived through were rolled for, not counted, and there is nothing to read them
from -- the journal keeps `mining.collapsed`, but who survived which one is
not a thing this column can honestly reconstruct.

The coin's own memory goes with it. `luck` kept a row per identity for the
matter `mine.death` -- how many rolls had missed -- and no code reads that
matter any more; left behind it would be a counter of a rule that no longer
exists, for whoever opens the table next.

Revision ID: a2f9c31b70d4
Revises: 9c4e7a1f2b63
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a2f9c31b70d4"
down_revision: str | None = "9c4e7a1f2b63"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "body",
        sa.Column("cave_ins", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("DELETE FROM luck WHERE matter = 'mine.death'")


def downgrade() -> None:
    #: The rolls that were missed are not coming back: the counter starts over
    #: for whoever goes back to the coin.
    op.drop_column("body", "cave_ins")
