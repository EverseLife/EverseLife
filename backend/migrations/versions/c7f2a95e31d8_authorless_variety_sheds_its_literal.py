# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""authorless variety sheds its literal name (D-251)

Base and wild cultivars used to store the vault's Russian display name in
`variety.name`, freezing one language into rows every language reads. The
name column is the creator's mark now: authorless rows carry NULL, and the
client says the word from the plants domain of
`/public/renames` -- `culture_id` plus the `wild` flag is the whole identity.
Author-named cultivars are marks, not copy, and keep their literal.

Revision ID: c7f2a95e31d8
Revises: c7d2a94f1e83
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c7f2a95e31d8"
down_revision: str | None = "c7d2a94f1e83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE variety SET name = NULL WHERE author_identity_id IS NULL")


def downgrade() -> None:
    #: Nothing to restore: the dropped literals were derivable display words,
    #: and the older code reading NULL falls back to its hybrid wording.
    pass
