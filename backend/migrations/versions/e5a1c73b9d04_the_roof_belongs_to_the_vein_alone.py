# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""the roof belongs to the vein alone

The working's stability is one number shared by everyone digging the vein
(D-188, D-099), and the session kept a copy of it. The copy was the truth the
engine worked from: a neighbour who opened the face at a hundred wrote
ninety-four back over somebody else's forty, and both were told a sign that
matched neither. It goes; `vein.roof` stays, and is read under the lock the
swing already holds.

Nothing is carried over. Every swing and every support wrote the vein as well,
so an open face at a vein with no roof yet is one nobody has shaken -- exactly
what an empty `vein.roof` means, and what `starting_roof` answers for.

Revision ID: e5a1c73b9d04
Revises: f3c7a0b5d21e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from src.units import SCALE_MIN

revision: str = "e5a1c73b9d04"
down_revision: str | None = "f3c7a0b5d21e"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_column("mining_session", "roof")


def downgrade() -> None:
    #: The copy comes back, and the only honest source for it is the vein it
    #: was a copy of. A working nobody has shaken carries no roof at all, and
    #: the number the old column held there came out of `starting_roof` -- that
    #: is, out of two constants of the vault, which a migration does not carry
    #: (D-065). Those faces get the bottom of the scale instead: the old engine
    #: reads that as a working already spent, so the next swing in one brings
    #: the roof down and the miner loses the haul of that session. It is the
    #: loud answer rather than the wrong one -- an invented roof would make a
    #: face accidentally immortal or bury it just the same, without saying so.
    #: Walking out banks the haul, and a face opened after the downgrade reads
    #: its roof from the vein as before.
    #:
    #: Closing those sessions instead would be quieter and worse. A face at a
    #: roofless vein is not always an empty one: a neighbour's cave-in clears
    #: the vein's roof (D-188) while everyone else at the face keeps digging,
    #: so some of these hold ore -- and a session closed here holds it for
    #: good, because `leave` refuses by state and nothing else opens that
    #: container. The bottom of the scale leaves the door open: walk out with
    #: the haul, or swing and pay for it.
    op.add_column("mining_session", sa.Column("roof", sa.Numeric(6, 2), nullable=True))
    op.execute(
        "UPDATE mining_session SET roof = (SELECT v.roof FROM vein v WHERE v.id = mining_session.vein_id)"
    )
    op.execute(
        sa.text("UPDATE mining_session SET roof = :floor WHERE roof IS NULL").bindparams(
            floor=SCALE_MIN
        )
    )
    op.alter_column("mining_session", "roof", existing_type=sa.Numeric(6, 2), nullable=False)
