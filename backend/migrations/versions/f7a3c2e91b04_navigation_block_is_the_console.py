"""The navigation block became the ship's console

«Навигационный блок» had no behaviour; it got one (D-230) -- a ship is
commanded from it -- and a name that says so: «Консоль управления кораблём».

An item's type key is the vault's own name, so a world that is not reseeded
would keep a machine nobody can craft or repair any more. The same rename the
shipbuilding stations went through (e4c7b1a90f52).

Revision ID: f7a3c2e91b04
Revises: d4b8e6c15a72
Create Date: 2026-08-23 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'f7a3c2e91b04'
down_revision: str | None = 'd4b8e6c15a72'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RENAMED = {
    "Навигационный блок": "Консоль управления кораблём",
}


def upgrade() -> None:
    for old, new in RENAMED.items():
        op.execute(f"UPDATE item SET type_key = '{new}' WHERE type_key = '{old}'")
        op.execute(f"UPDATE craft_batch SET output = '{new}' WHERE output = '{old}'")


def downgrade() -> None:
    for old, new in RENAMED.items():
        op.execute(f"UPDATE item SET type_key = '{old}' WHERE type_key = '{new}'")
        op.execute(f"UPDATE craft_batch SET output = '{old}' WHERE output = '{new}'")
