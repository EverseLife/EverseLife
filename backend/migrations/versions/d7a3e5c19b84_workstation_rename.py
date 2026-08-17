"""station renamed to рабочая станция

D-200 renamed the term: "станок" -> "рабочая станция". Two items carried the
old word in their own names, and item type keys are the vault's names, so a
world that is not reseeded would keep machines nobody can craft or repair any
more -- the recipe under the old key is gone.

Revision ID: d7a3e5c19b84
Revises: c9f4a2b81d30
Create Date: 2026-08-17 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'd7a3e5c19b84'
down_revision: str | None = 'c9f4a2b81d30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RENAMED = {
    "Монетный станок": "Монетная станция",
    "Автоматический станок": "Автоматическая станция",
}


def upgrade() -> None:
    for old, new in RENAMED.items():
        op.execute(f"UPDATE item SET type_key = '{new}' WHERE type_key = '{old}'")
        #: A batch remembers what it makes and on what: an unfinished mint job
        #: must not lose its machine over a rename.
        op.execute(f"UPDATE craft_batch SET output = '{new}' WHERE output = '{old}'")


def downgrade() -> None:
    for old, new in RENAMED.items():
        op.execute(f"UPDATE item SET type_key = '{old}' WHERE type_key = '{new}'")
        op.execute(f"UPDATE craft_batch SET output = '{old}' WHERE output = '{new}'")
