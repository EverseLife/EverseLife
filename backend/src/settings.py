from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OCTOVERSE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    #: The game-design vault's `build/` directory. The engine never reads the
    #: vault's Markdown -- only this directory (vault CLAUDE.md). The path is
    #: relative to `backend/`, and the vault lies next to the game repo, hence two steps up.
    vault_build: Path = Path("../../octoverse-game-design/build")

    database_url: str = "postgresql+asyncpg://octoverse:octoverse@localhost:5432/octoverse"
    redis_url: str = "redis://localhost:6379/0"

    #: How many journal jobs the worker takes per pass.
    job_batch: int = 64

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

    log_level: str = "INFO"

    @property
    def vault_build_path(self) -> Path:
        return self.vault_build.expanduser().resolve()


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
