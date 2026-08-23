"""Indexes the hot reads were missing (review 2026-08-23)

`ledger_entry(transaction_id)`: the balance trigger sums one transaction's
entries on every commit, and without the index that was a scan of the whole
journal. `node(created_at)`: `world.epoch()` is `min(created_at)` and every
look asks it.

Revision ID: b7d4e2f19c03
Revises: a3e1c7d2b9f4
Create Date: 2026-08-23 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b7d4e2f19c03"
down_revision: str | None = "a3e1c7d2b9f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_ledger_entry_transaction", "ledger_entry", ["transaction_id"])
    op.create_index("ix_node_created", "node", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_node_created", table_name="node")
    op.drop_index("ix_ledger_entry_transaction", table_name="ledger_entry")
