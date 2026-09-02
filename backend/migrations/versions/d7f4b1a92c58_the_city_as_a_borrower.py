"""A city's own loans are read on every pass, so they get an index (D-285).

Three readers ask what a city owes the capital -- the line that bounds its
borrowing, the trust that bounds the line, and the daily withholding from a
debtor's takings -- and every one of them runs at least once a day over every
city. `ix_loan_borrower` covers the citizen's side of the same table and does
nothing for this one: the query is by city and state.

Revision ID: d7f4b1a92c58
Revises: b6e3d1a94c07
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d7f4b1a92c58"
down_revision: str | None = "b6e3d1a94c07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_loan_city", "loan", ["city_id", "state"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_loan_city", table_name="loan")
