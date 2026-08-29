"""Which of a node's two surfaces a thing lies on

A node had one surface, and what it meant depended on whether a roof happened
to stand: goods on an empty plot lay in the open, and the same rows became
indoors the day a house went up over them. So a collapse could only guess what
it was burying, and the land window had no list of its own on a built-up plot
(D-244).

Now the two are told apart by a mark on the thing rather than by a second
store. A second store was tried first and taken back: some sixty places ask a
node's container "what is in this node" and mean everything -- the fire of an
eruption looking for what to burn, a rig looking for its coal, a brazier for
its fuel -- and a store that answered half of each question dropped the other
half quietly out of the world.

**Everything already lying keeps lying where it is.** `false` is indoors, and
that is right for a roofed node: those goods were under the roof. On a node
with **no** building the mark is not read at all -- there is no floor to be on,
so everything there is outdoors whatever the column says (`estate.split`).
Hence no data to move, and nothing to get wrong.

Revision ID: e2c74b105f83
Revises: c9f4a1e07b52
Create Date: 2026-08-28 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2c74b105f83"
down_revision: str | None = "c9f4a1e07b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "item",
        sa.Column("outdoors", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("item", "outdoors")
