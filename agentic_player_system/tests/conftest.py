# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Fixtures for the whole agent test tree: a store on a temp file, an agent in
it, and a names table that starts empty in every test."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aps import names
from aps.store import Store


@pytest.fixture(autouse=True)
def raw_ids():
    """The names table is process-global: every test starts without one."""
    names.reset()
    yield
    names.reset()


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "aps.sqlite3")


@pytest.fixture
def agent(store: Store) -> dict[str, Any]:
    return store.create_agent(
        {"name": "Тестер", "email": "t@example.com", "password": "secret", "goal": "основать город"}
    )
