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
from fastapi.middleware.cors import CORSMiddleware

from src.constants import HOLDER, Catalog, bootstrap, current_catalog
from src.engine import tick  # noqa: F401 — регистрирует обработчики заданий
from src.engine.jobs import require_handlers
from src.settings import settings

log = logging.getLogger(__name__)


def catalog() -> Catalog:
    """Каталоги процесса. Загружены при старте, живут в памяти."""
    return current_catalog()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    conf = settings()
    logging.basicConfig(level=conf.log_level)

    constants, loaded = bootstrap(conf.vault_build_path)
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

    from src.api import session
    from src.api.routes import public

    #: Клиент живёт на своём порту и ходит сюда за справочниками и стаканами.
    #: Порт клиента с чужой машины заранее не известен, поэтому кроме списка
    #: есть образец адреса локальной сети (`settings.allowed_origin_regex`).
    conf = settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=conf.allowed_origins,
        allow_origin_regex=conf.allowed_origin_regex,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    app.include_router(public.router)
    #: Единственная поверхность, где игрок действует. HTTP-методов у неё нет.
    app.include_router(session.router)

    @app.get("/health", tags=["служебное"])
    async def health() -> dict[str, object]:
        return {
            "ok": True,
            "constants": HOLDER.current().digest if HOLDER.is_loaded() else None,
        }

    return app


app = create_app()
