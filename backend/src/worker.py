"""Воркер: разбирает журнал заданий и тем самым двигает мир.

Запуск из `backend/`: `python -m src.worker`.

Воркеров может быть сколько угодно: `FOR UPDATE SKIP LOCKED` разводит их без
координации. Останов в любой момент безопасен — незавершённое задание
откатывается целиком и будет взято снова.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket

from src import herald
from src.constants import bootstrap
from src.db.base import dispose, session_factory
from src.engine import tick, utility
from src.engine.jobs import require_handlers, run_due
from src.runtime import WORKER_IDLE_SLEEP
from src.settings import settings

log = logging.getLogger("octoverse.worker")


async def main() -> None:
    conf = settings()
    logging.basicConfig(
        level=conf.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    constants, _ = bootstrap(conf.vault_build_path)
    require_handlers()
    log.info("константы %s, отпечаток %s", constants.source, constants.digest)

    factory = session_factory()
    worker_id = f"{socket.gethostname()}/{os.getpid()}"

    async with factory() as session, session.begin():
        await tick.ensure_scheduled(session)
        #: Счётчик быта тикает своим ритмом (`energy.meter_period`), но
        #: заводится здесь же: содержание узлов не должно зависеть от того,
        #: запускал ли кто-то сид (D-149).
        await utility.ensure_scheduled(session)
        #: Хроника наружу. Без вебхука в настройках задание просто молчит, но
        #: заводится всё равно: включение сводится к одной переменной среды.
        await herald.ensure_scheduled(session)
    log.info("часы мира заведены, воркер %s", worker_id)

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    try:
        while not stopping.is_set():
            done = await run_due(factory, limit=conf.job_batch, worker=worker_id)
            if done == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stopping.wait(), timeout=WORKER_IDLE_SLEEP)
    finally:
        await dispose()
        log.info("воркер остановлен")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
