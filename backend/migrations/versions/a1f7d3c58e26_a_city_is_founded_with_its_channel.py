# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A city is founded with its channel, not read into one

The city's official channel (D-222) used to be created the first time somebody
asked for it, and the asking is `look`: the unread count for the Net tab walks
the reader's channels, so the first citizen's first look at a young city
inserted a row. `city.found` writes the channel now, and the read only reads --
a read does not write, the rule of review 2026-08-23.

Every city standing before that change got its channel from a look, or is still
waiting for one. Both are given a channel here, so that the reading side may
stop creating: without this a city whose citizens had not looked yet would have
no channel at all, and nobody could post in it.

The name is the city's own, the same one the read used to write.

Revision ID: a1f7d3c58e26
Revises: e2f6b8c4d7a1
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a1f7d3c58e26"
down_revision: str | None = "e2f6b8c4d7a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO net_channel (id, name, about, city_id, created_at)
        SELECT gen_random_uuid(), city.name, '', city.id, now()
        FROM city
        LEFT JOIN net_channel ON net_channel.city_id = city.id
        WHERE net_channel.id IS NULL
        """
    )


def downgrade() -> None:
    """Nothing is taken back.

    Which channels this migration made cannot be told apart from those a look
    made before it, and dropping one takes its posts with it (`net_post` is
    `ON DELETE CASCADE`). A world that goes back to before this gets the same
    channels from the reading side again -- the world is eternal, and its
    words are not deleted to undo a revision (D-007).
    """
