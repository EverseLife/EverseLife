"""An account reads the world in a language of its own (D-251 wave III)

Two languages are equal (D-249), so the language is a property of whoever is
reading rather than a fact about the server. It sits on the account and not on
the identity: a person chooses it, and printing a new body must not switch the
world back into a language they do not read.

Existing accounts get Russian -- what they have been reading all along. There
is no `Accept-Language` here and there will not be: a browser's locale is not
a decision anybody made (the same line the landing holds, D-078).

Revision ID: c9e251b40d17
Revises: b7d251aa10c4
Create Date: 2026-08-30 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9e251b40d17"
down_revision: str | None = "b7d251aa10c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "account",
        sa.Column("locale", sa.String(length=8), nullable=False, server_default="ru"),
    )


def downgrade() -> None:
    op.drop_column("account", "locale")
