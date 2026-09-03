"""the deals of one buy order are read by an index

The statement opens a deposit row into the deals settled against its order
(D-190). Deals are never deleted, and the foreign key makes no index of its
own: without this every such opening scanned the whole history of trade.

Revision ID: b2d7f4e9c1a6
Revises: a71e5c8b0d34
Create Date: 2026-09-03 21:20:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'b2d7f4e9c1a6'
down_revision: str | None = 'a71e5c8b0d34'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index('ix_market_trade_buy_order', 'market_trade', ['buy_order_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_market_trade_buy_order', table_name='market_trade')
