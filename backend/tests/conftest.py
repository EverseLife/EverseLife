from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
async def session(loaded) -> AsyncIterator[AsyncSession]:
    """A clean database for every test.

    The schema is created from models, not by migrations: migrations are
    checked separately (`test_migrations.py`), and speed matters here.
    """
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=None)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"нет тестовой базы ({TEST_DATABASE_URL}): {exc}")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


@pytest_asyncio.fixture
async def factory(loaded) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A session factory over the same clean database -- for the job journal."""
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"нет тестовой базы ({TEST_DATABASE_URL}): {exc}")

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


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
