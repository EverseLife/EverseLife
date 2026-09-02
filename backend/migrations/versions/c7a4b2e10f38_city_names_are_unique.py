# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""city names are unique, ignoring case

A city's name becomes the name of its official channel (`city._open_channel`),
and the Net compares channel names case-insensitively: `net.channel.create`
refuses a second "Novograd" typed by hand. Founding did not, so two cities
could carry one name and hand out two channels carrying it -- the same
asymmetry between the two doors that the name's length had.

The index is what makes the rule hold. `city.establish` also asks, so that a
person gets words instead of a database error, but two foundings racing on the
same name pass that check together; only the index refuses the second.

Existing worlds: cities that already share a name are **not** renamed here. The
world is eternal (D-007) and a name is somebody's, not ours to change. The
upgrade fails on such a pair instead, and says which -- renaming is a decision
for whoever runs the world, and it is made before the upgrade, not by it.

Revision ID: c7a4b2e10f38
Revises: a1f7d3c58e26
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7a4b2e10f38"
down_revision: str | None = "a1f7d3c58e26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    #: Said before the index says it in Postgres's words: the report names the
    #: cities, so that whoever runs the world knows what to rename.
    clashes = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT string_agg(DISTINCT name, ', ') FROM city "
                "WHERE lower(name) IN ("
                "  SELECT lower(name) FROM city GROUP BY lower(name) HAVING count(*) > 1"
                ")"
            )
        )
        .scalar()
    )
    if clashes:
        raise RuntimeError(
            "cities sharing a name, case ignored: "
            f"{clashes}. Rename them before this upgrade -- a name is somebody's, "
            "and the migration does not choose who keeps it"
        )
    op.create_index("uq_city_name_lower", "city", [sa.text("lower(name)")], unique=True)


def downgrade() -> None:
    op.drop_index("uq_city_name_lower", table_name="city")
