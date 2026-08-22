# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The server's HTTP surface.

The boundary here is architectural, not configurational (01-tech-notes,
pattern 6; 60-meta/01-anti-cheat):

* **only reads may be public** -- prices, catalogs, the code;
* **there is no action API.** An in-person action goes only through the
  client session. As soon as a convenient REST for "make a swing" appears,
  mining turns into a script.

So this module will not have a single POST that changes the world. Actions
live on the WebSocket session and require the device fee (D-110).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src import herald  # noqa: F401 -- registers the chronicle handler
from src.constants import HOLDER, Catalog, bootstrap, current_catalog
from src.engine import tick  # noqa: F401 -- registers job handlers
from src.engine.jobs import require_handlers
from src.settings import settings

log = logging.getLogger(__name__)


def catalog() -> Catalog:
    """The process's catalogs. Loaded at startup, live in memory."""
    return current_catalog()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    conf = settings()
    logging.basicConfig(level=conf.log_level)

    constants, loaded = bootstrap(conf.vault_build_path)
    #: A missing handler must fail at startup, not in a tick.
    require_handlers()

    log.info(
        "constants loaded: %s (fingerprint %s), %s recipes",
        constants.source,
        constants.digest,
        len(loaded.recipes.recipes),
    )
    #: The server speaks first (D-226): the journal's listener lives with the process.
    from src.api import push

    await push.hub.start()
    try:
        yield
    finally:
        await push.hub.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Everse.Life",
        version="0.0.1",
        lifespan=lifespan,
        description=(
            "Read-only поверхность. Действия игрока идут через сессию клиента "
            "и в этом API отсутствуют намеренно."
        ),
    )

    from src.api import session
    from src.api.routes import public

    #: The client lives on its own port and comes here for catalogs and books.
    #: The client's port on another machine is not known in advance, so besides
    #: the list there is a local-network address pattern (`settings.allowed_origin_regex`).
    conf = settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=conf.allowed_origins,
        allow_origin_regex=conf.allowed_origin_regex,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    app.include_router(public.router)
    #: The only surface where the player acts. It has no HTTP methods.
    app.include_router(session.router)

    @app.get("/health", tags=["housekeeping"])
    async def health() -> dict[str, object]:
        from src.api import push

        return {
            "ok": True,
            "constants": HOLDER.current().digest if HOLDER.is_loaded() else None,
            #: The socket's tally (D-226, step 4): is the poll really gone.
            "session": push.hub.report(),
        }

    return app


app = create_app()
