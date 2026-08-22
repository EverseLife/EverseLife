"""The journal announces itself (D-226)

A trigger on `event` sends `NOTIFY event, <id>` with every commit; the API
process listens and tells the players concerned. No table changes.

Revision ID: a3e1c7d2b9f4
Revises: 92bd32b349cb
Create Date: 2026-08-23 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from src.db import ddl

revision: str = "a3e1c7d2b9f4"
down_revision: str | None = "92bd32b349cb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The rule lives in `src.db.ddl`, and the initial migration lays down the
    # whole set of rules by `ddl.statements()` -- so a database built from
    # scratch already has this trigger by the time this revision runs. Here it
    # is only for a database migrated before D-226; the drop makes the two
    # paths meet.
    op.execute(ddl.ANNOUNCE_FUNCTION)
    op.execute("DROP TRIGGER IF EXISTS event_announced ON event")
    op.execute(ddl.ANNOUNCE_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS event_announced ON event")
    op.execute("DROP FUNCTION IF EXISTS announce_event()")
