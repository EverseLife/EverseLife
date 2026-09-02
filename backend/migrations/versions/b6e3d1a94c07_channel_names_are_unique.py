# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""channel names are unique, ignoring case (D-284)

`net.channel.create` has always refused a name another channel holds, but only
by asking first: two people typing one name in the same second both pass the
question and both insert. And a city never asked at all -- `city._open_channel`
writes its channel from the model, so founding a city named after somebody's
existing channel handed the Net two channels of one name, which is exactly what
`create` refuses anybody who types it.

The index is what makes the rule hold rather than usually hold. It is the same
shape, and for the same reason, as `uq_city_name_lower` one revision back: a
name is what people find each other by, and two of them are indistinguishable
in every list the Net draws.

Existing worlds: channels that already share a name are **not** renamed here.
A name is somebody's, and which of two keeps it is a decision for whoever runs
the world, made before the upgrade rather than by it. The upgrade fails and
says which names clash.

Revision ID: b6e3d1a94c07
Revises: c7a4b2e10f38
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6e3d1a94c07"
down_revision: str | None = "c7a4b2e10f38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    #: Said in words before Postgres says it in its own: the report names the
    #: channels, so whoever runs the world knows what has to give.
    clashes = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT string_agg(DISTINCT name, ', ') FROM net_channel "
                "WHERE lower(name) IN ("
                "  SELECT lower(name) FROM net_channel GROUP BY lower(name) HAVING count(*) > 1"
                ")"
            )
        )
        .scalar()
    )
    if clashes:
        raise RuntimeError(
            "channels sharing a name, case ignored: "
            f"{clashes}. Free all but one of each before this upgrade. There is no "
            "rename in the game, for a channel or for a city, so this is an UPDATE "
            "on net_channel.name by hand -- a name is somebody's, and the migration "
            "does not choose who keeps it"
        )
    op.create_index(
        "uq_net_channel_name_lower", "net_channel", [sa.text("lower(name)")], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_net_channel_name_lower", table_name="net_channel")
