"""Counted things come to whole pieces (D-212)

No schema here: amounts keep living in thousandths of a unit, because measured
things -- ore, grain, water -- are honestly fractional. What this migration
does is clean up the world that grew before the rule: stacks of counted things
holding a fraction of a piece.

Rounding is **upwards**. Downwards would empty a stack of half an ingot into
nothing, and a stack of nothing breaks the `amount > 0` constraint the table
has carried from the beginning; upwards, the alpha's owners keep what they
have and gain at most a piece each. Which things are counted comes from the
vault snapshot the engine itself reads -- not from a list copied into this
file, which would rot the first time the vault changed.

Revision ID: d4a1c7f60b28
Revises: c9d4e2a7b1f3
Create Date: 2026-08-19 12:00:00.000000
"""
from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = 'd4a1c7f60b28'
down_revision: str | None = 'c9d4e2a7b1f3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Amounts are stored as integer thousandths of a unit (`src.units.AMOUNT_SCALE`).
SCALE = 1_000


def _measured() -> list[str]:
    """What the vault calls measured. An unreadable snapshot means no cleanup.

    The migration must run on a machine without the vault next to it -- CI
    builds the image from `vault/` in the repository, a developer points
    `OCTOVERSE_VAULT_BUILD` at the vault itself. Both are tried, and if neither
    answers the data is left alone: a migration that guesses which things are
    counted would round the wrong ones.
    """
    here = Path(__file__).resolve().parents[3]
    places = [
        Path(os.environ["OCTOVERSE_VAULT_BUILD"]) if os.environ.get("OCTOVERSE_VAULT_BUILD")
        else None,
        here / "vault",
        here / "backend" / "vault",
    ]
    for place in places:
        if place is None:
            continue
        path = Path(place) / "recipes.json"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as fh:
            return list(json.load(fh).get("bulk", []))
    return []


def upgrade() -> None:
    bulk = _measured()
    if not bulk:  # pragma: no cover -- no snapshot, nothing to round by
        return
    item = sa.table(
        "item",
        sa.column("amount", sa.BigInteger),
        sa.column("type_key", sa.String),
    )
    op.execute(
        item.update()
        .where(item.c.type_key.notin_(bulk), item.c.amount % SCALE != 0)
        #: `ceil(amount / scale) * scale` rather than integer division: `/`
        #: between a column and a number is **numeric** division in SQLAlchemy
        #: 2.0, so "(amount + scale - 1) / scale * scale" quietly returns the
        #: amount plus 0.999 instead of the next whole unit.
        .values(amount=sa.func.ceil(item.c.amount / SCALE) * SCALE)
    )


def downgrade() -> None:
    """Nothing to undo: the fractions are gone, and inventing them back would
    be inventing numbers nobody stored."""
