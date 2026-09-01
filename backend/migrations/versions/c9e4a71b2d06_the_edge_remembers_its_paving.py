"""The edge remembers its paving

Asphalt arrives (D-252): a second member of the paving class, laid by the very
same command -- and the only thing that makes it a thing of its own rather
than a duplicate with a new name (D-223) is that it decays slower. Which means
the edge has to remember what it was laid from: `road.decay_by_paving` is a
multiplier by paving kind, and a table nobody can look up a key for is not a
mechanic.

One nullable column. NULL is the world's own road -- laid by nobody at the
seeding, decaying at the base rate -- and every edge that lived before this
migration is exactly that, so no backfill: the past was laid by nobody we can
name. The mark is written by the laying (the dominant kind of what was spent),
survives resurfacing, and is wiped when the tier is lost -- the covering went
with it.

Revision ID: c9e4a71b2d06
Revises: b1c7d2e4f905
Create Date: 2026-09-01 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9e4a71b2d06"
down_revision: str | None = "b1c7d2e4f905"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("edge", sa.Column("paving", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("edge", "paving")
