"""HTTP-поверхность сервера.

Граница здесь архитектурная, а не настроечная (01-tech-notes, паттерн 6;
60-meta/01-anti-cheat):

* **публичным может быть только чтение** — цены, справочники, кодекс;
* **API действий не существует.** Присутственное действие идёт только через
  сессию клиента. Стоит появиться удобному REST для «сделать удар», и добыча
  превращается в скрипт.

Поэтому в этом модуле не будет ни одного POST, меняющего мир. Действия живут
на WebSocket-сессии и требуют платы устройства (D-110).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from octoverse.constants import HOLDER, Catalog, bootstrap
from octoverse.engine import tick  # noqa: F401 — регистрирует обработчики заданий
from octoverse.engine.jobs import require_handlers
from octoverse.settings import settings

log = logging.getLogger(__name__)

#: Каталоги загружаются при старте и живут в памяти процесса.
CATALOG: Catalog | None = None


def catalog() -> Catalog:
    if CATALOG is None:  # pragma: no cover
        raise RuntimeError("каталоги не загружены")
    return CATALOG


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global CATALOG
    conf = settings()
    logging.basicConfig(level=conf.log_level)

    constants, loaded = bootstrap(conf.vault_build_path)
    CATALOG = loaded
    #: Обработчик, которого нет, обязан падать при старте, а не в тике.
    require_handlers()

    log.info(
        "константы загружены: %s (отпечаток %s), рецептов %s",
        constants.source,
        constants.digest,
        len(loaded.recipes.recipes),
    )
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="OctoVerse",
        version="0.0.1",
        lifespan=lifespan,
        description=(
            "Read-only поверхность. Действия игрока идут через сессию клиента "
            "и в этом API отсутствуют намеренно."
        ),
    )

    from octoverse.api.routes import public

    app.include_router(public.router)

    @app.get("/health", tags=["служебное"])
    async def health() -> dict[str, object]:
        return {
            "ok": True,
            "constants": HOLDER.current().digest if HOLDER.is_loaded() else None,
        }

    return app


app = create_app()
