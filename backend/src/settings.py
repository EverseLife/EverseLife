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
    #: никогда — только этот каталог (CLAUDE.md вольта). Путь считается от
    #: `backend/`, а вольт лежит рядом с репозиторием игры, отсюда два шага вверх.
    vault_build: Path = Path("../../octoverse-game-design/build")

    database_url: str = "postgresql+asyncpg://octoverse:octoverse@localhost:5432/octoverse"
    redis_url: str = "redis://localhost:6379/0"

    #: Сколько заданий журнала воркер берёт за один проход.
    job_batch: int = 64

    #: Откуда пускать браузерный клиент. По умолчанию — только dev-сервер
    #: фронтенда на этой же машине: пускать кого попало в сессию игрока незачем.
    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    #: Плюс к списку — вся локальная сеть: играть заходят с телефона и соседней
    #: машины, а их адреса заранее не известны. Диапазоны частные (RFC 1918),
    #: поэтому из интернета так не зайти. Настоящее опознание приедет к Э7.
    #:
    #: Отдельно 26.0.0.0/8 — сеть Radmin VPN, через которую играют не соседи по
    #: комнате. Диапазон **не** частный: он просто не маршрутизируется в
    #: интернете, и попасть в него можно только вступив в саму сеть VPN. То
    #: есть здесь мы полагаемся на её список участников, а не на топологию, —
    #: и это ещё одна причина, по которой заглушка опознания живёт ровно до Э7.
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

    log_level: str = "INFO"

    @property
    def vault_build_path(self) -> Path:
        return self.vault_build.expanduser().resolve()


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
