# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Secrets at rest: the players' passwords and the provider key are sealed.

The store is SQLite on a volume; a backup of it was every agent's game
password and the LLM key in the clear (review 2026-08-23). With
`APS_SECRET_KEY` set (a Fernet key, `Fernet.generate_key()`), the secret
columns hold `enc:...`; without it they stay plain and the panel says so.
Sealing is transparent to callers: `seal()` on the way in, `reveal()` on
the way out, and a plain value from before the key was set still reveals.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)
PREFIX = "enc:"


def _cipher():
    key = os.environ.get("APS_SECRET_KEY", "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet

        return Fernet(key.encode())
    except Exception as trouble:  # noqa: BLE001 -- a bad key is a config error, said once
        log.error("APS_SECRET_KEY unusable: %s", trouble)
        return None


def sealing() -> bool:
    """Whether secrets are sealed at rest right now."""
    return _cipher() is not None


def seal(value: str | None) -> str | None:
    if value is None or value.startswith(PREFIX):
        return value
    cipher = _cipher()
    if cipher is None:
        return value
    return PREFIX + cipher.encrypt(value.encode()).decode()


def reveal(value: str | None) -> str | None:
    if value is None or not value.startswith(PREFIX):
        return value
    cipher = _cipher()
    if cipher is None:
        raise RuntimeError("a sealed secret and no APS_SECRET_KEY to open it")
    return cipher.decrypt(value[len(PREFIX) :].encode()).decode()
