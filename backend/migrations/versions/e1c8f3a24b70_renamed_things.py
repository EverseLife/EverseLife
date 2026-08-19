"""Things renamed in the vault keep the world they were in

Two names changed, and both for the same reason -- the old one said something
untrue about the thing:

* **flax** was called "wild flax" from the days when the stalks were only
  picked off a meadow. Since then it has become a crop: there are flax seeds, a
  bed to sow them in and a cycle to wait out, and the bed handed out "wild"
  flax, which reads as a data error rather than a harvest;
* **the backpack** became "a simple backpack": the first of a row, and the row
  needs the plain name free.

The vault renames the thing; this carries the world over, so that nobody's
stack, order or find is orphaned by a rename. Everything that stores a goods
name by value is touched: what lies in hands and chests, what stands in the
books, what a batch is making and what it wrote off, and the find waiting on
the ground. Event history is left alone on purpose -- it says what was true
when it happened.

Revision ID: e1c8f3a24b70
Revises: d4a1c7f60b28
Create Date: 2026-08-19 16:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e1c8f3a24b70'
down_revision: str | None = 'd4a1c7f60b28'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: old name -> new name.
RENAMED = {
    "Дикий лён": "Лён",
    "Рюкзак": "Простой рюкзак",
}

#: table -> the column holding a goods name by value.
BY_VALUE = (
    ("item", "type_key"),
    ("market_order", "type_key"),
    ("market_reservation", "type_key"),
    ("market_trade", "type_key"),
    ("craft_batch", "output"),
    ("forage", "found"),
)


def _rename(was: str, now: str) -> None:
    for table, column in BY_VALUE:
        op.execute(
            sa.text(f"UPDATE {table} SET {column} = :now WHERE {column} = :was").bindparams(
                sa.bindparam("now", now), sa.bindparam("was", was)
            )
        )
    #: What a batch wrote off is a map keyed by the goods name: the key moves,
    #: the number stays.
    op.execute(
        sa.text(
            "UPDATE craft_batch "
            "SET spent = (spent - :was) || jsonb_build_object(:now, spent -> :was) "
            "WHERE spent ? :was"
        ).bindparams(sa.bindparam("now", now), sa.bindparam("was", was))
    )


def upgrade() -> None:
    for was, now in RENAMED.items():
        _rename(was, now)


def downgrade() -> None:
    for was, now in RENAMED.items():
        _rename(now, was)
