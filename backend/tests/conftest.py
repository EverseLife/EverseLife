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
#: Вольт лежит рядом с репозиторием игры, а сервер — в его подкаталоге `backend/`.
VAULT_BUILD = Path(
    os.environ.get(
        "OCTOVERSE_VAULT_BUILD", BACKEND / ".." / ".." / "octoverse-game-design" / "build"
    )
)

#: База для тестов. Если её нет — тесты, которым она нужна, пропускаются,
#: а чистая логика проверяется всё равно.
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
    """Чистая база на каждый тест.

    Схема создаётся из моделей, а не миграциями: миграции проверяются отдельно
    (`test_migrations.py`), а здесь важна скорость.
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
    """Фабрика сессий поверх той же чистой базы — для журнала заданий."""
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
