# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The D-251 data migration is pinned by data, not by a rehearsal.

`b7d251aa10c4` carries every stored Russian vault name onto its id. Row counts
cannot see a value that stayed Russian in one table while its counterpart
moved -- that is how an open state build order would have become unpayable --
and a freshly seeded world cannot see what only a world with a past contains.
Both holes cost a real defect apiece, so both are pinned here: the tests plant
the rows the pre-release engine wrote, run the upgrade, and read the ids back.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND = Path(__file__).resolve().parent.parent
BASE_URL = os.environ.get(
    "EVERSELIFE_TEST_DATABASE_URL",
    "postgresql+asyncpg://everselife:everselife@localhost:5432/everselife_test",
)
DB_NAME = BASE_URL.rsplit("/", 1)[1] + "_d251"
URL = BASE_URL.rsplit("/", 1)[0] + "/" + DB_NAME
ADMIN_URL = BASE_URL.rsplit("/", 1)[0] + "/postgres"

BEFORE_D251 = "a8d2480f11ce"


def _alembic(direction: str, target: str) -> None:
    done = subprocess.run(  # noqa: S603 -- our own alembic, fixed args
        [sys.executable, "-m", "alembic", direction, target],
        cwd=BACKEND,
        env={**os.environ, "EVERSELIFE_DATABASE_URL": URL, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, (done.stderr or done.stdout)[-2000:]


async def _fresh() -> bool:
    """A database of this test's own, dropped and made again."""
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{DB_NAME}"'))
            await conn.execute(text(f'CREATE DATABASE "{DB_NAME}"'))
    except OSError:  # pragma: no cover -- environment, not the rule
        return False
    finally:
        await admin.dispose()
    return True


async def _drop() -> None:
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{DB_NAME}"'))
    await admin.dispose()


@pytest.mark.asyncio
async def test_upgrade_translates_every_stored_surface() -> None:
    """One representative surface per translation mechanism.

    Not every column: plain ones share a single UPDATE, JSONB keys share a
    rekey, JSONB values share a set. What is checked is that each mechanism
    runs and reverses -- including `payload.building_kind`, which the first
    version of the migration forgot and which would have left every open city
    build order unpayable, its escrow hanging.
    """
    if not await _fresh():
        pytest.skip("нет тестовой базы")
    _alembic("upgrade", BEFORE_D251)

    engine = create_async_engine(URL)
    node_id = str(uuid.uuid4())
    order_id = str(uuid.uuid4())
    try:
        async with engine.begin() as conn:
            #: The rows exactly as the pre-release engine wrote them.
            await conn.execute(
                text(
                    "INSERT INTO node (id, key, name, layer, planet, area_m2, properties)"
                    " VALUES (:id, 'terra.test.d251', 'Тест', 'location', 'terra', 100,"
                    " CAST(:properties AS jsonb))"
                ),
                {
                    "id": node_id,
                    "properties": json.dumps(
                        {"участок": True, "вода": "река", "значок": "мастерская"},
                        ensure_ascii=False,
                    ),
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO work_order (id, kind, state, node_id, payload, tariff)"
                    " VALUES (:id, 'building_build', 'open', :node,"
                    " CAST(:payload AS jsonb), 100)"
                ),
                {
                    "id": order_id,
                    "node": node_id,
                    "payload": json.dumps(
                        {"type_key": "Кирпич", "building_kind": "каменный"},
                        ensure_ascii=False,
                    ),
                },
            )

        _alembic("upgrade", "head")
        async with engine.connect() as conn:
            properties = (
                await conn.execute(
                    text("SELECT properties FROM node WHERE id = :id"), {"id": node_id}
                )
            ).scalar_one()
            assert properties == {"plot": True, "water": "river", "emblem": "workshop"}
            payload = (
                await conn.execute(
                    text("SELECT payload FROM work_order WHERE id = :id"), {"id": order_id}
                )
            ).scalar_one()
            assert payload == {"type_key": "brick", "building_kind": "stone"}

        _alembic("downgrade", BEFORE_D251)
        async with engine.connect() as conn:
            payload = (
                await conn.execute(
                    text("SELECT payload FROM work_order WHERE id = :id"), {"id": order_id}
                )
            ).scalar_one()
            assert payload == {"type_key": "Кирпич", "building_kind": "каменный"}
    finally:
        await engine.dispose()
        await _drop()


@pytest.mark.asyncio
async def test_two_spellings_on_one_shelf_do_not_break_the_upgrade() -> None:
    """A goods name inside a unique key may already hold its neighbour's target.

    The dev world keeps «Навигационный блок» and «Консоль управления кораблём»
    on one library shelf -- `f7a3c2e91b04` renamed the items and left the
    shelf alone -- and both map onto `ship_console`. Without the collision
    sweep the upgrade aborts on the unique constraint: in production, halfway.
    Found by running the migration against the real dev database; the first
    rehearsal used a freshly seeded world, which has no past to trip over.
    """
    if not await _fresh():
        pytest.skip("нет тестовой базы")
    _alembic("upgrade", BEFORE_D251)

    engine = create_async_engine(URL)
    node_id = str(uuid.uuid4())
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO node (id, key, name, layer, planet, area_m2, properties)"
                    " VALUES (:id, 'terra.test.shelf', 'Полка', 'location', 'terra', 100,"
                    " '{}'::jsonb)"
                ),
                {"id": node_id},
            )
            for recipe in ("Навигационный блок", "Консоль управления кораблём"):
                await conn.execute(
                    text(
                        "INSERT INTO library_entry (id, node_id, recipe)"
                        " VALUES (:id, :node, :recipe)"
                    ),
                    {"id": str(uuid.uuid4()), "node": node_id, "recipe": recipe},
                )

        _alembic("upgrade", "head")
        async with engine.connect() as conn:
            shelf = (
                (
                    await conn.execute(
                        text("SELECT recipe FROM library_entry WHERE node_id = :node"),
                        {"node": node_id},
                    )
                )
                .scalars()
                .all()
            )
        assert shelf == ["ship_console"], "две записи об одном рецепте схлопываются в одну"
    finally:
        await engine.dispose()
        await _drop()


#: A memo is **not** migrated, and that is worth writing down where the next
#: person will look for it. The ledger is append-only -- `db.ddl` puts a
#: trigger on `ledger_transaction` that refuses UPDATE outright -- so renaming
#: a key inside a posting's memo means rewriting history, and the database
#: says no. Rightly: an audit trail that can be rewritten is not one.
#:
#: So the compatibility lives in the reader instead: new postings are keyed in
#: ASCII, old ones keep «основание», and `finance.statement` accepts either.
#: See `test_ledger.test_a_statement_reads_the_ground_however_it_was_keyed`.
