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

    #: Каталог `build/` вольта гейм-дизайна. Markdown вольта движок не читает
    #: никогда — только этот каталог (CLAUDE.md вольта).
    vault_build: Path = Path("../octoverse-game-design/build")

    database_url: str = "postgresql+asyncpg://octoverse:octoverse@localhost:5432/octoverse"
    redis_url: str = "redis://localhost:6379/0"

    #: Сколько заданий журнала воркер берёт за один проход.
    job_batch: int = 64

    log_level: str = "INFO"

    @property
    def vault_build_path(self) -> Path:
        return self.vault_build.expanduser().resolve()


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
