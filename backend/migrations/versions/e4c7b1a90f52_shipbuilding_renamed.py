"""The two shipbuilding stations renamed

«Космодром» became «Космическая верфь» -- a ship is not only moored there but
laid down and grown there (D-202) -- and the ground «Верфь», where hulls and
engines are made, became «Космическая мастерская».

An item's type key is the vault's own name, so a world that is not reseeded
would keep machines nobody can craft or repair any more: the recipes under the
old keys are gone. The same rename the mint went through (D-200).

Revision ID: e4c7b1a90f52
Revises: a2d5f38b71c4
Create Date: 2026-08-18 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'e4c7b1a90f52'
down_revision: str | None = 'a2d5f38b71c4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RENAMED = {
    "Космодром": "Космическая верфь",
    "Верфь": "Космическая мастерская",
}


def upgrade() -> None:
    for old, new in RENAMED.items():
        op.execute(f"UPDATE item SET type_key = '{new}' WHERE type_key = '{old}'")
        #: A batch remembers what it makes: an unfinished build must not lose
        #: its output over a rename.
        op.execute(f"UPDATE craft_batch SET output = '{new}' WHERE output = '{old}'")


def downgrade() -> None:
    for old, new in RENAMED.items():
        op.execute(f"UPDATE item SET type_key = '{old}' WHERE type_key = '{new}'")
        op.execute(f"UPDATE craft_batch SET output = '{old}' WHERE output = '{new}'")
