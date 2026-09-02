# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""installed equipment (D-278)

A machine or a piece of furniture either stands in the building it was put
up in, or lies as cargo. Until now the two were one: whatever reached the
node's store counted as standing, so a station dropped on the floor was
installed by the drop. Everything already in a **node's** store today was
put there to stand -- the seed's machines, the relics, whatever a player put
up before the distinction existed -- and is stood up here; a pocket, a hold
and a chest never stood anything. Heaps of ore stood up along with the
workbench beside them lie back down in the seed's catch-up, which knows the
catalog where a migration does not.

Revision ID: e2f6b8c4d7a1
Revises: a1c4f7b9d2e6
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2f6b8c4d7a1"
down_revision: str | None = "a1c4f7b9d2e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "item",
        sa.Column("installed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    #: Only what a node's store holds: that is where things stood until now.
    op.execute(
        "UPDATE item SET installed = true FROM container "
        "WHERE item.container_id = container.id AND container.kind = 'node'"
    )


def downgrade() -> None:
    op.drop_column("item", "installed")
