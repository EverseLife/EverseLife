# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""one authorless variety per culture

The base cultivar and the wild ancestor are created lazily at first need
(D-057, D-260), and two first needs happen in the same second: both sessions
select nothing and both insert. The partial unique index makes the second
insert refuse instead of doubling the row; `engine.breed` catches the refusal
under a savepoint and rereads the winner.

The world is eternal (D-007), so a world where the race already fired carries
the doubles, and the index will not go on until they are gone. For each
`(culture_id, wild)` pair the eldest authorless row is kept -- everything ever
pointing at a twin (seed lots, sown plots, nursery parents) is repointed to it
first. Seed lots repointed this way do not merge with the keeper's lots lying
beside them: stacking happens on write, and nothing here rewrites items --
they simply stop pretending to be different cultivars.

Revision ID: c7d2a94f1e83
Revises: b3e7d19a5c42
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d2a94f1e83"
down_revision: str | None = "b3e7d19a5c42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The eldest authorless row of each `(culture_id, wild)` pair stays; the rest
#: are the twins a race inserted. Repeated per statement rather than kept in a
#: temp table: every statement then stands on its own, whatever transaction
#: shape the migration runs under.
EXTRA = """
WITH keeper AS (
    SELECT DISTINCT ON (culture_id, wild) id, culture_id, wild
    FROM variety
    WHERE author_identity_id IS NULL
    ORDER BY culture_id, wild, created_at, id
), extra AS (
    SELECT twin.id AS old_id, keeper.id AS new_id
    FROM variety AS twin
    JOIN keeper ON keeper.culture_id = twin.culture_id AND keeper.wild = twin.wild
    WHERE twin.author_identity_id IS NULL AND twin.id <> keeper.id
)
"""

#: Everything in the schema that points at a cultivar -- a hybrid's own
#: pedigree included: nothing reads `variety.parent_*` today, but the world
#: is eternal (D-007) and this migration runs once. Rewriting the pedigree
#: does not disturb the CTE: it keys on columns the updates never touch.
POINTERS = (
    ("item", "variety_id"),
    ("plot", "variety_id"),
    ("nursery", "parent_a_id"),
    ("nursery", "parent_b_id"),
    ("nursery", "result_variety_id"),
    ("variety", "parent_a_id"),
    ("variety", "parent_b_id"),
)


def upgrade() -> None:
    for table, column in POINTERS:
        op.execute(
            EXTRA + f"UPDATE {table} SET {column} = extra.new_id "  # noqa: S608
            f"FROM extra WHERE {table}.{column} = extra.old_id"
        )
    op.execute(EXTRA + "DELETE FROM variety USING extra WHERE variety.id = extra.old_id")
    op.create_index(
        "uq_variety_authorless",
        "variety",
        ["culture_id", "wild"],
        unique=True,
        postgresql_where=sa.text("author_identity_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_variety_authorless", table_name="variety")
