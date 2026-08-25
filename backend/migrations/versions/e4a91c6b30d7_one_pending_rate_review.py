"""One pending rate review, not one per restart

`bank.schedule_review` runs at the start of every process and counted its
period from the second of the call: another second, another dedup key, another
review. Two processes per deploy and a deploy a day, and the journal filled
with reviews -- all of them fired, and the chronicle carried a line about the
key rate several times over, for a number the vault moves once in three days.

The scheduling is fixed in the engine (the day is the anchor, and a pending
chain stops a second one). What the engine cannot fix is what is already
queued: those reviews are in the journal of every world that ran before the
fix, and every one of them would still fire.

So the queue is cut back to **one**: the earliest pending review survives -- it
is the one the world was waiting for -- and the rest are cancelled. Cancelled
rather than deleted: the journal is append-only in spirit, and a job that never
ran is a fact worth keeping.

Revision ID: e4a91c6b30d7
Revises: c8f1a37b52d9
Create Date: 2026-08-25 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'e4a91c6b30d7'
down_revision: str | None = 'c8f1a37b52d9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KIND = "bank.rate_review"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE job SET state = 'cancelled'
        WHERE kind = '{KIND}' AND state = 'pending'
          AND id <> (
              SELECT id FROM job
              WHERE kind = '{KIND}' AND state = 'pending'
              ORDER BY run_at, id
              LIMIT 1
          )
        """
    )


def downgrade() -> None:
    """Nothing. Bringing back a queue of duplicate reviews is not a state any
    world wants to be in, and the one that survived is enough to keep the
    policy running."""
