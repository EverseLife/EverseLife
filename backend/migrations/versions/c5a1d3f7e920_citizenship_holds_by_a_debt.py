"""Citizenship is held by a debt, not by a term or a delay (D-281).

The door gives citizenship to every newcomer now, and leaving is a deletion in
the moment it is asked for: the filed declaration with its `city.exit_delay`
is gone, and so is the print obligation that held one until a date. Both
columns go, and the exits queued against them are cancelled -- their citizens
simply stay citizens, one click from leaving whenever nothing is owed.

The two print-condition laws leave the vault with the same decision, so cities
that had answered them keep dangling keys in `city.laws`. Swept here: a law of
that name arriving later must not inherit an answer given to a rule that no
longer exists.

Revision ID: c5a1d3f7e920
Revises: e2f6b8c4d7a1
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5a1d3f7e920"
down_revision: str | None = "e2f6b8c4d7a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    #: Before the columns, while the job still has something to point at: a
    #: pending exit is somebody's unfinished intention, and it ends as
    #: cancelled rather than as a failure with no handler.
    op.execute(
        "UPDATE job SET state = 'cancelled', finished_at = now() "
        "WHERE kind = 'city.citizenship_exit' AND state IN ('pending', 'running')"
    )
    op.drop_column("citizen", "leaving_at")
    op.drop_column("citizen", "bound_until")
    op.execute("UPDATE city SET laws = laws - 'spawn_citizenship' - 'spawn_term'")


def downgrade() -> None:
    op.add_column("citizen", sa.Column("bound_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("citizen", sa.Column("leaving_at", sa.DateTime(timezone=True), nullable=True))
