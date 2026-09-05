# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
"""a trail is worn in

D-319: the two lowest surfaces are nobody's work. An edge laid with the world
is `wild`, feet wear a `trail` into one that is walked, and a trail nobody
walks grows over again. What decides between the two is a counter on the edge:
one per arrival over it, less `path.fade_per_day` every day, never below
nought. The surface column is a varchar, not a native enum (`enum_column`), so
the new value needs no type change -- only the counter is new.

Every standing edge starts untrodden at nought. The seed of the old world laid
its wild ways as `trail`; they keep the word until the first daily tick, which
reads the counter and finds them unwalked.

Revision ID: 3e7c1a9d5b42
Revises: c7d41f8a3b62
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "3e7c1a9d5b42"
down_revision: str | None = "c7d41f8a3b62"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "edge",
        sa.Column("wear", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    #: The old engine knows no `wild`: what was worn back into it reads as
    #: the trail it would have been, and nothing else is lost.
    op.execute("UPDATE edge SET surface = 'trail' WHERE surface = 'wild'")
    op.drop_column("edge", "wear")
