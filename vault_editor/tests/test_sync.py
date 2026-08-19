"""`deploy/sync-vault.py` -- the one way the snapshot gets refreshed.

The script belongs to the repository, not to the editor, but it is covered here:
this is the only suite in the repository that runs on plain Python, and the
editor's «Синхронизировать» button is the thing that runs the script most often.
Loaded by path because the file name has a dash in it and is meant as a command,
not as a module.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import vaultfile as vault

SCRIPT = vault.REPO / "deploy" / "sync-vault.py"


@pytest.fixture(scope="session")
def sync():
    if not SCRIPT.exists():
        pytest.skip(f"нет скрипта: {SCRIPT}")
    spec = importlib.util.spec_from_file_location("sync_vault", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_build(root: Path, names) -> Path:
    (root / "build").mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / "build" / name).write_text(f'{{"file": "{name}"}}', encoding="utf-8")
    return root


def test_the_snapshot_is_exactly_what_the_engine_reads(sync, tmp_path: Path):
    source = make_build(tmp_path / "vault", [*sync.SNAPSHOT, "лишнее.json"])
    repo = tmp_path / "repo"
    repo.mkdir()

    assert sync.copy_snapshot(source, repo) == list(sync.SNAPSHOT)
    assert sorted(p.name for p in (repo / "vault").iterdir()) == sorted(sync.SNAPSHOT)
    assert (repo / "vault" / sync.SNAPSHOT[0]).read_text(encoding="utf-8")


def test_an_incomplete_build_is_not_carried_over(sync, tmp_path: Path):
    """Half a snapshot is worse than none.

    The engine would read today's recipes against yesterday's constants, and the
    fingerprint in `/api/health` would stop meaning anything.
    """
    source = make_build(tmp_path / "vault", sync.SNAPSHOT[:1])
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(FileNotFoundError, match="incomplete"):
        sync.copy_snapshot(source, repo)
    assert not (repo / "vault").exists()


def test_the_snapshot_is_replaced_not_merged(sync, tmp_path: Path):
    source = make_build(tmp_path / "vault", sync.SNAPSHOT)
    repo = tmp_path / "repo"
    (repo / "vault").mkdir(parents=True)
    (repo / "vault" / sync.SNAPSHOT[0]).write_text("вчерашнее", encoding="utf-8")

    sync.copy_snapshot(source, repo)
    assert "вчерашнее" not in (repo / "vault" / sync.SNAPSHOT[0]).read_text(encoding="utf-8")


def test_a_missing_vault_is_reported_not_guessed(sync, tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["sync-vault.py", "--vault", str(tmp_path / "нет-такого")])
    assert sync.main() == 1
    assert "vault not found" in capsys.readouterr().err
