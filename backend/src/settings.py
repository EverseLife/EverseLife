# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EVERSELIFE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    #: The game-design vault's `build/` directory. The engine never reads the
    #: vault's Markdown -- only this directory (vault CLAUDE.md). The path is
    #: relative to `backend/`, and the vault lies next to the game repo, hence two steps up.
    vault_build: Path = Path("../../everselife-vault/build")

    database_url: str = "postgresql+asyncpg://everselife:everselife@localhost:5432/everselife"
    redis_url: str = "redis://localhost:6379/0"

    #: How many database connections one process keeps, and how many more it
    #: may take at a peak. This is the width of the server: a session command
    #: holds a connection for the whole of its transaction, so `pool_size +
    #: max_overflow` is how many players are served at the same instant.
    #: Several processes (`uvicorn --workers`) each hold their own pool, and
    #: their sum may not exceed the database's `max_connections`.
    db_pool_size: int = 5
    db_max_overflow: int = 10

    #: How many journal jobs the worker takes per pass.
    job_batch: int = 64
    #: Jobs a worker process runs at once. `FOR UPDATE SKIP LOCKED` keeps the
    #: claims apart; the daily tick is many small jobs, not one (wave 4), so
    #: a long step no longer holds the arrivals and the finishes behind it.
    job_concurrency: int = 4

    #: Where the browser client is allowed from. By default only the frontend
    #: dev server on this same machine: no reason to let just anyone into a player's session.
    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    #: Plus the list -- the whole local network: people come to play from a
    #: phone and the neighbouring machine, and their addresses are not known in
    #: advance. The ranges are private (RFC 1918), so one cannot enter this way
    #: from the internet. Real identification arrives by E7.
    #:
    #: Separately 26.0.0.0/8 -- the Radmin VPN network through which people who
    #: are not roommates play. The range is **not** private: it simply is not
    #: routed on the internet, and one can get into it only by joining the VPN
    #: itself. I.e. here we rely on its member list, not on topology -- one more
    #: reason the identification stub lives exactly until E7.
    allowed_origin_regex: str = (
        r"^http://("
        r"localhost"
        r"|127\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
        r"|26\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r")(:\d+)?$"
    )

    #: The Discord chronicle channel webhook (`herald/`). Empty -- the herald is
    #: silent, and that is the normal state: the production alpha has a
    #: community server, not every copy on somebody's laptop.

    discord_webhook: str = ""

    #: Which revision of the source this process was built from. AGPL §13
    #: asks that a player be offered the source **of this version**, and a bare
    #: link to the repository does not say which one. CI already tags images
    #: with `github.sha`; the same value belongs here. Empty -- the answer is
    #: honest about not knowing, which is the case for a local run.
    release: str = ""

    log_level: str = "INFO"

    @property
    def vault_build_path(self) -> Path:
        return self.vault_build.expanduser().resolve()


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
