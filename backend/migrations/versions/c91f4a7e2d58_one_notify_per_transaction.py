"""One journal notification per transaction (D-226, wave 4)

The announce trigger sent the row id; Postgres queues each distinct payload
separately, so a tick writing a thousand rows queued a thousand entries
behind the notify lock. An empty payload collapses to one per transaction;
the listener reads the journal from its mark and needs no ids.

Revision ID: c91f4a7e2d58
Revises: b7d4e2f19c03
Create Date: 2026-08-23 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from src.db import ddl

revision: str = "c91f4a7e2d58"
down_revision: str | None = "b7d4e2f19c03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(ddl.ANNOUNCE_FUNCTION)


def downgrade() -> None:
    op.execute(
        """
CREATE OR REPLACE FUNCTION announce_event() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('event', NEW.id::text);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""
    )
