from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from src.db import ddl
from src.models import Base  # noqa: F401 -- fills metadata
from src.settings import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    #: No `include_name` here, unlike below: alembic refuses to autogenerate
    #: offline at all ("autogenerate can't use as_sql=True"), so the filter
    #: would never be asked for. `--sql` only replays migrations already written.
    context.configure(
        url=settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    #: `include_name` is the filter, not a preference: without it
    #: `alembic revision --autogenerate` sees the journal's partitions as
    #: tables nobody declared and writes `op.drop_table` for each. It is the
    #: same object `tests/test_migrations.py` compares under, so the migration
    #: that test asks for is the migration it would then accept.
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_name=ddl.include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
