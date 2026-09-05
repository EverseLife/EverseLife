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
    context.configure(
        url=settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
        include_name=ddl.include_name,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


#: `compare_type`, `compare_server_default` and `include_name` are all off by
#: default, and all three are on here for one reason: what autogeneration does
#: not compare it does not write, and what it can compare against nothing at
#: all it offers to drop. `tests/test_migrations.py` asks for the same three,
#: so a difference it reports is a difference `--autogenerate` can actually
#: write down -- the advice in its failure message has to lead somewhere.
#:
#: `include_name` is the last part of that to arrive, and it was the sharpest.
#: The journal's partitions (`event_YYYYMM`, `event_default`) are tables
#: Postgres keeps under the parent and the models declare only the parent, so
#: autogeneration read them as tables nobody wanted and offered to drop them:
#: sixteen operations against a database that was perfectly correct. Applied
#: unread, that revision deletes events, and the world is eternal (D-007). The
#: filter is not written here but taken from `db.ddl`, beside the DDL that
#: makes the partitions, so this file and the test cannot drift into filtering
#: differently -- which is precisely how the hole lasted: the test had the
#: filter, this file did not, and the test stayed green pointing at a command
#: that would have emptied the journal.
#:
#: The offline path carries the three for symmetry alone. Alembic refuses to
#: autogenerate with `as_sql=True`, so nothing there ever asks for them.
def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
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
