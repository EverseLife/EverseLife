# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""law choices are keys: build_permit and body_print

Two code-laws were free text the engine read by Russian substring -- «гражд»
in the value meant citizens, anything unrecognised meant everyone. A player of
another language could not set either, and a typo silently opened the city.
They are choices with keys now (`nobody`, `citizens`, `everyone`), so what the
cities already decided is rewritten to the key that **means what the engine
did with it**, not to what it looks like: the rules below are the old readers,
line for line, so no city's behaviour changes across this migration.

The two laws differ on the empty value and that difference is kept: no
`build_permit` meant everyone (the ring was open), no `body_print` meant
nobody (the city paid for no one). A value already a key is left alone, so
the migration may run twice.

Revision ID: b4e91c07af52
Revises: c5a1d3f7e920
"""

from __future__ import annotations

import json
from collections.abc import Callable

import sqlalchemy as sa
from alembic import op

revision: str = "b4e91c07af52"
down_revision: str | None = "c5a1d3f7e920"
branch_labels: str | None = None
depends_on: str | None = None

NOBODY, CITIZENS, EVERYONE = "nobody", "citizens", "everyone"
KEYS = {NOBODY, CITIZENS, EVERYONE}


def _permit(value: str) -> str:
    """`may_take_city_land` as it read the text before this."""
    said = value.strip().lower()
    if not said:
        return EVERYONE
    if said.startswith("никто") or said in ("нет", "-"):
        return NOBODY
    if "гражд" in said:
        return CITIZENS
    return EVERYONE


def _print(value: str) -> str:
    """`death._city_pays` as it read the text before this."""
    said = value.strip().lower()
    if said in ("", "нет", "-"):
        return NOBODY
    if "гражд" in said:
        return CITIZENS
    return EVERYONE


def _rewrite(read: dict[str, Callable[[str], str]], bind=None) -> None:
    """Walk the cities and put the keys in place of the words.

    `bind` is the connection to walk on; without one it is alembic's own. The
    parameter exists so a test can hand it a session's connection: a clean
    database has no cities, so the run the house rule asks for proves the
    migration applies and nothing about what it rewrites.
    """
    bind = bind if bind is not None else op.get_bind()
    rows = bind.execute(sa.text("SELECT id, laws FROM city")).fetchall()
    for city_id, laws in rows:
        held = dict(laws or {})
        after = dict(held)
        for law, decide in read.items():
            if law not in held:
                continue
            value = str(held[law] or "")
            #: Already a key: the migration is idempotent, and a fresh world
            #: seeded after this change needs no touching.
            after[law] = value if value in KEYS else decide(value)
        if after != held:
            bind.execute(
                sa.text("UPDATE city SET laws = CAST(:laws AS jsonb) WHERE id = :id"),
                {"laws": json.dumps(after, ensure_ascii=False), "id": city_id},
            )


def upgrade() -> None:
    _rewrite({"build_permit": _permit, "body_print": _print})


def downgrade() -> None:
    """Back to the words the old engine read.

    One word per key, the one the vault offered: a city that had some other
    spelling of the same meaning does not get its own wording back, and cannot
    -- the key does not remember it. The behaviour is the same either way,
    which is what a downgrade owes.
    """
    words = {
        "build_permit": {NOBODY: "никто", CITIZENS: "граждане", EVERYONE: "все"},
        "body_print": {NOBODY: "нет", CITIZENS: "гражданам", EVERYONE: "всем"},
    }
    rows = op.get_bind().execute(sa.text("SELECT id, laws FROM city")).fetchall()
    for city_id, laws in rows:
        held = dict(laws or {})
        after = dict(held)
        for law, back in words.items():
            if law in held:
                after[law] = back.get(str(held[law]), str(held[law]))
        if after != held:
            op.get_bind().execute(
                sa.text("UPDATE city SET laws = CAST(:laws AS jsonb) WHERE id = :id"),
                {"laws": json.dumps(after, ensure_ascii=False), "id": city_id},
            )
