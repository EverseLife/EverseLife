from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

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
TEST_DATABASE_URL = os.environ.get(
    "OCTOVERSE_TEST_DATABASE_URL",
    "postgresql+asyncpg://octoverse:octoverse@localhost:5432/octoverse_test",
)


@pytest.fixture(scope="session")
def loaded() -> tuple[Constants, Catalog]:
    return bootstrap(VAULT_BUILD.resolve())


@pytest.fixture(scope="session")
def constants(loaded: tuple[Constants, Catalog]) -> Constants:
    return loaded[0]


@pytest.fixture(scope="session")
def catalog(loaded: tuple[Constants, Catalog]) -> Catalog:
    return loaded[1]


@pytest_asyncio.fixture
async def database(loaded) -> AsyncIterator[AsyncEngine]:
    """One engine and one clean schema per test.

    The schema is created from models, not by migrations: migrations are checked
    separately (`test_migrations.py`), and speed matters here.

    **Everything that talks to the database in a test comes through here.** It
    used to be two fixtures, each with its own engine and each rebuilding the
    schema, and that cost the suite twenty-two failures at a time:
    `drop_all` wants an `AccessExclusiveLock` on tables another connection is
    already inserting into with a `RowExclusiveLock`, so Postgres picked one of
    them and killed it. The tests were fine -- every one of them passed when run
    alone -- which is the signature of a harness racing itself rather than a
    defect in the code under test.

    `NullPool` is the other half of the same fix. With a pool, a connection
    outlives the test that opened it and waits in the pool holding whatever the
    server has not reaped yet; the next test's `drop_all` then blocks on a
    connection belonging to a test that has already finished. Without a pool a
    connection dies with its session, and there is nothing left to fight.
    """
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"нет тестовой базы ({TEST_DATABASE_URL}): {exc}")

    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session(database: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A clean database for every test."""
    async with async_sessionmaker(database, expire_on_commit=False)() as db:
        yield db


@pytest_asyncio.fixture
async def factory(database: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """A session factory over the same clean database -- for the job journal.

    The same engine as `session`, which is what "the same" was always meant to
    say: a test that takes both gets one schema, not two rebuilds of it.
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
