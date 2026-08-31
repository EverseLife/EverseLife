"""A rate decision keeps its reasons, not a Russian sentence (D-251 wave IV)

`rate_decision.why` was rendered at the moment of the decision, in the world's
default language: an archive row cannot be re-rendered per reader, so whoever
read the bank afterwards read it in Russian whatever language they had chosen.

The reasons go into a column of their own as keys and numbers --
`[{"say": "bank-why-rate-base", "args": {"rate": 12.0}}, ...]` -- and the
sentence is assembled at the edge, in the language of whoever is asking.

The old text column stays. Rows written before this migration have no keys to
say, and an audit trail with a hole in it is worse than one line in the wrong
language: the edge falls back to `why` when `why_said` is empty. Nothing writes
`why` any more, so the fallback empties itself as the history rolls forward.

Revision ID: d1f7a94c62b8
Revises: c9e251b40d17
Create Date: 2026-08-31 03:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1f7a94c62b8"
down_revision: str | None = "c9e251b40d17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    #: Nullable rather than defaulted to `[]`: "written before the keys" and
    #: "decided for no stated reason" are different facts, and the edge tells
    #: them apart to know whether the old line is worth falling back to.
    op.add_column(
        "rate_decision",
        sa.Column("why_said", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rate_decision", "why_said")
