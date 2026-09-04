# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Secrets at rest: passwords and the provider key sealed with APS_SECRET_KEY,
and a rotated key that still opens what the old one sealed."""

from __future__ import annotations

from pathlib import Path

import pytest

from aps.store import Store


def test_secrets_are_sealed_at_rest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    Fernet = pytest.importorskip("cryptography.fernet").Fernet

    from aps import secrets

    monkeypatch.setenv("APS_SECRET_KEY", Fernet.generate_key().decode())
    assert secrets.sealing()
    store = Store(tmp_path / "sealed.sqlite3")
    made = store.create_agent({"name": "А", "email": "a@x", "password": "hunter2"})
    raw = store.db.execute("SELECT password FROM agents WHERE id = ?", (made["id"],)).fetchone()[0]
    assert raw.startswith("enc:") and "hunter2" not in raw
    assert store.agent(made["id"])["password"] == "hunter2"
    store.set_setting("llm.api_key", "sk-secret")
    stored = store.db.execute("SELECT value FROM settings WHERE key = 'llm.api_key'").fetchone()[0]
    assert stored.startswith("enc:") and store.setting("llm.api_key") == "sk-secret"


def test_a_rotated_key_still_opens_what_the_old_one_sealed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fernet = pytest.importorskip("cryptography.fernet")
    from aps import secrets

    old, new = fernet.Fernet.generate_key().decode(), fernet.Fernet.generate_key().decode()
    monkeypatch.setenv("APS_SECRET_KEY", old)
    sealed = secrets.seal("hunter2")
    #: New key first, old kept: the store keeps reading, seals with the new.
    monkeypatch.setenv("APS_SECRET_KEY", f"{new},{old}")
    assert secrets.reveal(sealed) == "hunter2"
