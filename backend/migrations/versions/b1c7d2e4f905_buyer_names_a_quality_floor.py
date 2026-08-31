"""The buyer names a quality floor

Five tiers were the only language a request had, and a buyer who needs iron no
worse than 70 could not say so: "хорошее" (60-79) promises worse than they want
and "отличное" (80-100) hides half the supply. To gather the demand they would
have to stand in two books at once.

So a buy order gains a floor of its own (D-239). A sell order keeps its tier --
what a seller has is a lot, not a wish -- and the floor stays empty there.
Empty on the buys already resting in the book as well: their tier's own start
is read as the floor, which is exactly what their tier button meant, so nothing
that is queued changes its terms under its owner.

The demand index comes with it: a buy with a floor is a bid in every tier that
can satisfy it, so the tier drops out of the key that finds it. And one more
for the price beside a goods name in the picker -- the last deal under that
name, whatever its tier, which the book index cannot answer because the tier
stands between the name and the clock.

What does change under a resting order: its floor now reaches **above** its own
tier, so a buy queued in "хорошее" will take "отличное" as well. The buyer pays
the price they named and gets better goods than they asked for, which is the
whole point of the floor -- but it is a wider order than the one they placed,
and worth knowing when reading a book that lived through the migration.

Revision ID: b1c7d2e4f905
Revises: d1f7a94c62b8
Create Date: 2026-08-28 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b1c7d2e4f905'
down_revision: str | None = 'd1f7a94c62b8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("market_order", sa.Column("min_quality", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "min_quality_non_negative", "market_order", "min_quality IS NULL OR min_quality >= 0"
    )
    op.create_index(
        "ix_market_order_demand", "market_order", ["node_id", "type_key", "side", "state"]
    )
    #: The last deal per goods name in a node, whatever its tier: the picker
    #: asks on every market event, deals are never deleted, and the book index
    #: cannot answer it -- the tier stands between the name and the clock.
    op.execute(
        "CREATE INDEX ix_market_trade_last ON market_trade "
        "(node_id, type_key, at DESC, id DESC)"
    )


def downgrade() -> None:
    op.drop_index("ix_market_trade_last", table_name="market_trade")
    op.drop_index("ix_market_order_demand", table_name="market_order")
    op.drop_constraint("min_quality_non_negative", "market_order", type_="check")
    op.drop_column("market_order", "min_quality")
