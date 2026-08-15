"""ore species: "Ore" becomes "Iron ore"

Ore species (D-151). The world is eternal, no wipes (D-007) -- so the
renaming of raw material must reach already existing worlds rather than
remain a rule for new ones. Otherwise ore in chests would stop smelting at
all: smelting now has its own input for each metal.

**State** is renamed, not journals: events and postings are immutable by
construction, and in them the old name stays forever -- that is what it was that day.

Revision ID: f7b2c40de915
Revises: d4c1a90f2b73
Create Date: 2026-08-13
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f7b2c40de915"
down_revision: str | None = "d4c1a90f2b73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "Руда"
NEW = "Железная руда"

#: Where the goods name lies in the world state.
TABLES = (
    ("item", "type_key"),
    ("market_order", "type_key"),
    ("market_reservation", "type_key"),
    ("market_trade", "type_key"),
    ("craft_batch", "output"),
    ("vein", "resource"),
)


def upgrade() -> None:
    for table, column in TABLES:
        op.execute(
            f"UPDATE {table} SET {column} = '{NEW}' WHERE {column} = '{OLD}'"  # noqa: S608
        )
    #: The coal gully became a mine: same place, more honest title -- there is
    #: a mine there, not a ravine, and a road leads to it.

    op.execute(
        "UPDATE node SET name = 'Угольная шахта' "
        "WHERE key = 'terra.coal' AND name = 'Угольная балка'"
    )


def downgrade() -> None:
    for table, column in TABLES:
        op.execute(
            f"UPDATE {table} SET {column} = '{OLD}' WHERE {column} = '{NEW}'"  # noqa: S608
        )
    op.execute(
        "UPDATE node SET name = 'Угольная балка' "
        "WHERE key = 'terra.coal' AND name = 'Угольная шахта'"
    )
