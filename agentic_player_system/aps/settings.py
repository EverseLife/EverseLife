# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Process settings: read once from the environment (and `.env` next to the package)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    admin_login: str
    admin_password: str
    game_url: str
    db_path: Path
    session_source: Path
    host: str
    port: int
    log_level: str
    #: Provider defaults from the environment; the panel's values win when set.
    llm_base_url: str
    llm_model: str
    llm_api_key: str

    @property
    def ws_url(self) -> str:
        base = self.game_url.rstrip("/")
        return ("ws" + base[4:] if base.startswith("http") else base) + "/session/ws"


def load() -> Settings:
    _load_dotenv()
    env = os.environ.get
    return Settings(
        admin_login=env("APS_ADMIN_LOGIN", "admin"),
        admin_password=env("APS_ADMIN_PASSWORD", ""),
        game_url=env("APS_GAME_URL", "http://localhost:8000"),
        db_path=(ROOT / env("APS_DB", "./aps.sqlite3")).resolve(),
        session_source=(ROOT / env("APS_SESSION_SOURCE", "../backend/src/api/commands")).resolve(),
        host=env("APS_HOST", "127.0.0.1"),
        port=int(env("APS_PORT", "8100")),
        log_level=env("APS_LOG_LEVEL", "INFO"),
        llm_base_url=env("APS_LLM_BASE_URL", "https://api.deepseek.com/v1"),
        llm_model=env("APS_LLM_MODEL", "deepseek-chat"),
        llm_api_key=env("APS_LLM_API_KEY", ""),
    )
