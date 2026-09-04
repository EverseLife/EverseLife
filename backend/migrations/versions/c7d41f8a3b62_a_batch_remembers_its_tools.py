# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""a batch remembers its tools

D-309: a tool in a batch's requirements now wears by the hours worked, so the
batch must remember which items it works with. `tool_item_id` held one id and
only when the client named it -- the requirement the engine resolved itself was
never written down, and nothing ever read the column back. It gives way to a
list filled by the engine: a requirement list may name two tools, and both set
the ceiling, so both wear.

Nothing is carried over: the old column was never read, and the batches running
at the moment of the upgrade finish on the tools they never recorded -- one
batch's worth of free wear, once.

The index comes with it and for the same reason (D-310): "what is this body at"
is asked in every `look`, and it now counts a build and a road among the kinds
-- the two longest-lived pending jobs there are. Without an index on the body
that question runs off (state, kind) and filters by hand, over a set that grows
with the number of players.

Revision ID: c7d41f8a3b62
Revises: b3e9d0c47a15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7d41f8a3b62"
down_revision: str | None = "b3e9d0c47a15"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "craft_batch",
        sa.Column("tool_item_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.drop_column("craft_batch", "tool_item_id")
    op.create_index(
        "ix_job_body_running",
        "job",
        ["body_id"],
        unique=False,
        postgresql_where=sa.text("state in ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("ix_job_body_running", table_name="job")
    #: Back to one id and back to nobody reading it: the old engine wears no
    #: tool at all, so what the list held is not lost, it stops mattering.
    op.add_column("craft_batch", sa.Column("tool_item_id", sa.Uuid(), nullable=True))
    op.drop_column("craft_batch", "tool_item_ids")
