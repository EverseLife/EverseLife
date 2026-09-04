# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""the working keeps its own measure

D-302: the roof was never hidden. `look` gives the vein's richness as a
number, the starting roof is a public formula over two public constants, and a
support landed on the public ceiling exactly -- so a lone miner had the one
hidden number of the mechanic by arithmetic before the first swing. The vein
gets a salt that never leaves the server, and its starting roof, its timber
ceiling and the lie its sign tells are all drawn from it.

Existing veins get one each, so no two workings share a measure. Random by
design: a salt derived from anything the client already has is no salt.

Revision ID: b3e9d0c47a15
Revises: e5a1c73b9d04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b3e9d0c47a15"
down_revision: str | None = "e5a1c73b9d04"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    #: Added nullable, filled, then closed: the column has no default in the
    #: model either -- a vein is born with its salt (`default=uuid.uuid4`), and
    #: a server default would quietly cover a creator that forgot to.
    op.add_column("vein", sa.Column("roof_salt", sa.Uuid(), nullable=True))
    op.execute("UPDATE vein SET roof_salt = gen_random_uuid() WHERE roof_salt IS NULL")
    op.alter_column("vein", "roof_salt", existing_type=sa.Uuid(), nullable=False)


def downgrade() -> None:
    #: The measure is lost, and with it every working's own starting roof and
    #: ceiling: the old engine computes both from richness alone. Nothing is
    #: stranded by that -- a shaken roof is stored as a number and stays one.
    op.drop_column("vein", "roof_salt")
