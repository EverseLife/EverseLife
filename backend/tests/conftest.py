"""Fixtures of the suite, and the price of a clean database.

Seven hundred tests each want the world empty. How that emptiness is reached is
the whole running time of the suite: rebuilding the schema before every test
cost two seconds a test and sixteen minutes a run -- and it grew as it went,
because seven hundred `drop`/`create` cycles bloat the Postgres catalogue.

Three things are done instead, and all three matter:

* **the schema is built once per run** -- from the models, as before, so a
  change to the models still reaches the tests;
* **the tables are emptied by `TRUNCATE`, and only those with rows in them.**
  A test touches a dozen tables out of fifty-eight, and asking which ones costs
  three milliseconds against the three hundred that emptying all of them costs;
* **one engine with a connection pool for the whole run.** Opening a connection
  to Postgres costs some seventy milliseconds here, and a test opens several.
  This is why the run has a single event loop (`asyncio_default_test_loop_scope`
  in `pyproject.toml`): an asyncpg connection belongs to the loop that opened
  it, and a per-test loop cannot inherit a pool.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.constants import Catalog, Constants, bootstrap
from src.models import Base

BACKEND = Path(__file__).resolve().parents[1]
#: The vault lies next to the game repo, and the server in its `backend/` subdirectory.
VAULT_BUILD = Path(
    os.environ.get(
        "OCTOVERSE_VAULT_BUILD", BACKEND / ".." / ".." / "octoverse-game-design" / "build"
    )
)

#: The test database. If it is absent, tests that need it are skipped, and
#: pure logic is checked anyway.
BASE_DATABASE_URL = os.environ.get(
    "OCTOVERSE_TEST_DATABASE_URL",
    "postgresql+asyncpg://octoverse:octoverse@localhost:5432/octoverse_test",
)

#: Under `pytest -n` every worker gets a database of its own: the suite empties
#: tables between tests, and two workers on one database would empty each
#: other's world. The name carries the worker's (`gw0`, `gw1`); the databases
#: themselves are created by `pytest_configure` below.
WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
TEST_DATABASE_URL = f"{BASE_DATABASE_URL}_{WORKER}" if WORKER else BASE_DATABASE_URL


async def _create_database(url: str) -> None:
    """Create the worker's database if it is not there yet.

    Only under `-n`: a run in one process uses the database it was given, and
    creating that one is the developer's business, or CI's, as before.
    """
    import asyncpg
    from sqlalchemy.engine import make_url

    target = make_url(url)
    admin = target.set(database="postgres")
    try:
        connection = await asyncpg.connect(
            user=admin.username,
            password=admin.password,
            host=admin.host,
            port=admin.port,
            database=admin.database,
        )
    except Exception:  # noqa: BLE001
        #: No server at all. The fixtures will say so once and skip, which is
        #: better than a wall of connection errors from every worker.
        return
    try:
        await connection.execute(f'CREATE DATABASE "{target.database}"')
    except asyncpg.DuplicateDatabaseError:
        pass
    finally:
        await connection.close()


def pytest_configure(config: pytest.Config) -> None:
    if WORKER:
        asyncio.run(_create_database(TEST_DATABASE_URL))


@pytest.fixture(scope="session")
def loaded() -> tuple[Constants, Catalog]:
    return bootstrap(VAULT_BUILD.resolve())


@pytest.fixture(scope="session")
def constants(loaded: tuple[Constants, Catalog]) -> Constants:
    return loaded[0]


@pytest.fixture(scope="session")
def catalog(loaded: tuple[Constants, Catalog]) -> Catalog:
    return loaded[1]


#: Which tables the previous test left something in. One query for all of them:
#: fifty-eight `EXISTS` over empty tables cost about three milliseconds, while
#: emptying all fifty-eight blindly costs three hundred.
_DIRTY = text(
    " UNION ALL ".join(
        f"SELECT '{name}' WHERE EXISTS (SELECT 1 FROM \"{name}\")"
        for name in Base.metadata.tables
    )
)

#: A hung test would otherwise hold the lock and the next wipe would wait for it
#: forever. Better a clear refusal after ten seconds.
_LOCK_TIMEOUT = text("SET LOCAL lock_timeout = '10s'")

#: The schema is built by the first test that needs it and lives to the end of
#: the run. Module state, and rightly so: one process is one database, and under
#: `-n` every worker has a process and a database of its own.
_schema_built = False


async def _build(engine: AsyncEngine) -> None:
    global _schema_built
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    _schema_built = True


async def reset(engine: AsyncEngine) -> None:
    """Empty the database, building the schema if this is the first call.

    Emptying is `TRUNCATE` and not `DELETE`: the three append-only tables
    (`event`, `ledger_entry`, `ledger_transaction`) carry a trigger that refuses
    a delete, and `TRUNCATE` is not a delete -- it fires no row triggers.
    `RESTART IDENTITY` returns the two counters to one, exactly as a freshly
    created schema would.
    """
    if not _schema_built:
        await _build(engine)
        return

    try:
        async with engine.begin() as connection:
            await connection.execute(_LOCK_TIMEOUT)
            dirty = (await connection.execute(_DIRTY)).scalars().all()
            if dirty:
                await connection.execute(
                    text(
                        "TRUNCATE "
                        + ", ".join(f'"{name}"' for name in dirty)
                        + " RESTART IDENTITY CASCADE"
                    )
                )
    except DatabaseError:
        #: The schema went away under us, or never matched the models. Build it
        #: again rather than fail every test that follows.
        await _build(engine)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine(loaded) -> AsyncIterator[AsyncEngine]:
    """One engine for the whole run.

    A pool, and no longer `NullPool`. The pool used to be half of a deadlock:
    `drop_all` wants an `AccessExclusiveLock` on tables that a connection left
    over from a finished test was still holding, so Postgres killed one of the
    two and the suite lost twenty-two tests at a time. There is no `drop_all`
    between tests any more, and connections idle in the pool hold no locks --
    there is nothing left to fight over.
    """
    #: Ten connections at most. A test opens two or three at once, and the
    #: ceiling matters under `-n`: workers share one Postgres, and its
    #: `max_connections` is a hundred by default.
    made = create_async_engine(TEST_DATABASE_URL, pool_size=5, max_overflow=5)
    try:
        await _build(made)
    except Exception as exc:  # noqa: BLE001
        await made.dispose()
        pytest.skip(f"нет тестовой базы ({TEST_DATABASE_URL}): {exc}")

    try:
        yield made
    finally:
        await made.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def database(engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """An empty database for every test.

    The schema is created from models, not by migrations: migrations are checked
    separately (`test_migrations.py`), and speed matters here.

    **Everything that talks to the database in a test comes through here.** It
    used to be two fixtures, each with its own engine and each rebuilding the
    schema, and that cost the suite twenty-two failures at a time -- the tests
    were fine, every one of them passed when run alone, which is the signature
    of a harness racing itself rather than a defect in the code under test.
    """
    await reset(engine)
    yield engine


@pytest_asyncio.fixture(loop_scope="session")
async def session(database: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A clean database for every test."""
    async with async_sessionmaker(database, expire_on_commit=False)() as db:
        yield db


@pytest_asyncio.fixture(loop_scope="session")
async def factory(database: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """A session factory over the same clean database -- for the job journal.

    The same engine as `session`, which is what "the same" was always meant to
    say: a test that takes both gets one database, not two rebuilds of it.
    """
    return async_sessionmaker(database, expire_on_commit=False)


@pytest.fixture
def own_plot(session: AsyncSession):
    """Hand a plot to a person the only way the world allows (D-198).

    Title to land is issued by a city, so the plot first becomes civic and only
    then somebody's. Tests used to call `world.claim_node` -- taking wild land
    on foot -- and that road no longer exists.
    """
    import uuid

    from src.engine import world

    async def give(node, identity):
        if node.owner_city_id is None:
            node.owner_city_id = uuid.uuid4()
            await session.flush()
        return await world.grant_node(session, node, identity)

    return give
