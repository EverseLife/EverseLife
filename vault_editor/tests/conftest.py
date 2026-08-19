"""Fixtures for the editor's own tests.

The tests never touch the real vault: they copy `data/recipes.yaml` into a
temporary directory and edit the copy. What they check is exactly what the
editor promises -- that an edit changes one line and nothing else.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

EDITOR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EDITOR))

import vaultfile as vault  # noqa: E402  -- the path has to be set first


@pytest.fixture(scope="session")
def source() -> Path:
    try:
        return vault.vault_root() / "data" / "recipes.yaml"
    except vault.VaultError as error:
        pytest.skip(str(error))


@pytest.fixture
def recipes(tmp_path: Path, source: Path) -> Path:
    """A copy of the real recipe file, line endings and all."""
    target = tmp_path / "recipes.yaml"
    shutil.copy2(source, target)
    return target


@pytest.fixture(autouse=True)
def backups_elsewhere(tmp_path: Path, monkeypatch) -> Path:
    """Backups of a test edit belong in the test's own directory."""
    place = tmp_path / "backups"
    monkeypatch.setattr(vault, "BACKUPS", place)
    return place
