"""Миграции обязаны совпадать с моделями.

Мир вечный, вайпов не бывает (D-007) — значит расхождение схемы с кодом не
чинится пересозданием базы. Проверка ловит его в тот день, когда оно появилось.

Тест смотрит на базу, накатанную миграциями (`alembic upgrade head`), и требует,
чтобы автогенерация не нашла ни одного отличия.
"""

from __future__ import annotations

import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from src.models import Base

MIGRATED_URL = os.environ.get(
    "OCTOVERSE_MIGRATED_DATABASE_URL",
    "postgresql+asyncpg://octoverse:octoverse@localhost:5432/octoverse",
)


def _differences(connection) -> list:
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return compare_metadata(context, Base.metadata)


async def test_схема_из_миграций_совпадает_с_моделями() -> None:
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


async def test_правила_базы_на_месте() -> None:
    """Триггеры сходимости и неизменяемости обязаны быть в накатанной базе."""
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
