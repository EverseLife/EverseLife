# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""pioneer index (D-259)

The pioneer lookup scans `knowledge` by `kind = 'recipe' AND discovered`
per key on every read of the `knowledge` part. The only index so far is
the unique `(identity_id, kind, key)`, which leads with the identity and
cannot serve that scan -- a partial index over the discovered rows can.

Revision ID: 742125dad53f
Revises: c7f2a95e31d8
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "742125dad53f"
#: Re-parented at merge time: the branch grew from b3e7d19a5c42 while the
#: variety pair (c7d2a94f1e83, c7f2a95e31d8) landed in parallel -- the chain
#: stays linear, and the index depends on nothing they change.
down_revision: str | None = "c7f2a95e31d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_knowledge_pioneer",
        "knowledge",
        ["kind", "key"],
        unique=False,
        postgresql_where=sa.text("discovered"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_pioneer",
        table_name="knowledge",
        postgresql_where=sa.text("discovered"),
    )
