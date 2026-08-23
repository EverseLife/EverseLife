# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Migrations must match the models.

The world is eternal, no wipes (D-007) -- so a schema divergence from code
is not fixed by recreating the database. The check catches it the day it appears.

The test looks at a database upgraded by migrations (`alembic upgrade head`)
and requires that autogeneration finds not a single difference.
"""

from __future__ import annotations

import os
import re

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from src.models import Base

MIGRATED_URL = os.environ.get(
    "EVERSELIFE_MIGRATED_DATABASE_URL",
    "postgresql+asyncpg://everselife:everselife@localhost:5432/everselife",
)


PARTITION = re.compile(r"^event_(\d{6}|default)$")
PARTITION_INDEX = re.compile(r"^event_(\d{6}|default)_")


def _differences(connection) -> list:
    #: The journal's partitions (`event_YYYYMM`, `event_default`) are tables
    #: Postgres makes under the parent; the models know only the parent.
    def include(name, type_, parent_names):
        if name is None:
            return True
        partition = (type_ == "table" and PARTITION.match(name)) or (
            type_ == "index" and PARTITION_INDEX.match(name)
        )
        return not partition

    context = MigrationContext.configure(
        connection, opts={"compare_type": True, "include_name": include}
    )
    return compare_metadata(context, Base.metadata)


async def test_schema_from_migrations_matches_models() -> None:
    engine = create_async_engine(MIGRATED_URL)
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda c: inspect(c).get_table_names())
            if "alembic_version" not in tables:
                pytest.skip("база не накатана миграциями: `alembic upgrade head`")
            diff = await connection.run_sync(_differences)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"нет базы {MIGRATED_URL}: {exc}")
    finally:
        await engine.dispose()

    assert not diff, (
        "схема разошлась с моделями: "
        + "; ".join(str(item) for item in diff)
        + ". Нужна миграция: `alembic revision --autogenerate`"
    )


async def test_database_rules_in_place() -> None:
    """The balance and immutability triggers must be in the upgraded database."""
    from sqlalchemy import text

    engine = create_async_engine(MIGRATED_URL)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            )
            names = {row[0] for row in rows}
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"нет базы {MIGRATED_URL}: {exc}")
    finally:
        await engine.dispose()

    assert {
        "ledger_entry_balanced",
        "ledger_entry_append_only",
        "event_append_only",
        "ledger_transaction_append_only",
    } <= names
