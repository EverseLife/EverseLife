# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The worker: drains the job journal and thereby moves the world.

Run from `backend/`: `python -m src.worker`.

There can be any number of workers: `FOR UPDATE SKIP LOCKED` separates them
without coordination. Stopping at any moment is safe -- an unfinished job
rolls back whole and will be taken again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
from pathlib import Path

from src import herald
from src.constants import bootstrap
from src.db.base import dispose, session_factory
from src.engine import tick, utility
from src.engine.jobs import require_handlers, run_due
from src.runtime import WORKER_IDLE_SLEEP
from src.settings import settings

log = logging.getLogger("everselife.worker")


#: Touched by every pass of every lane: the container's healthcheck reads its
#: age ("без worker мир стоит", and nobody noticed for a day before this).
HEARTBEAT = Path(os.environ.get("EVERSELIFE_WORKER_HEARTBEAT", "/tmp/everselife-worker.beat"))


def _beat() -> None:
    with contextlib.suppress(OSError):
        HEARTBEAT.touch()


async def main() -> None:
    conf = settings()
    logging.basicConfig(
        level=conf.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    constants, _ = bootstrap(conf.vault_build_path)
    require_handlers()
    log.info("constants %s, fingerprint %s", constants.source, constants.digest)

    factory = session_factory()
    worker_id = f"{socket.gethostname()}/{os.getpid()}"

    async with factory() as session, session.begin():
        await tick.ensure_scheduled(session)
        #: The household meter ticks at its own rhythm (`energy.meter_period`)
        #: but is started right here: node maintenance must not depend on
        #: whether somebody ran the seed (D-149).
        await utility.ensure_scheduled(session)
        #: The chronicle going out. Without a webhook in settings the job is
        #: simply silent, but it is started anyway: enabling comes down to one env variable.

        await herald.ensure_scheduled(session)
    log.info("world clock started, worker %s", worker_id)

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    async def lane(index: int) -> None:
        """One of `job_concurrency` loops over the queue; each claim is its own
        transaction with `SKIP LOCKED`, so lanes never take the same job."""
        name = f"{worker_id}/{index}"
        while not stopping.is_set():
            _beat()
            done = await run_due(factory, limit=conf.job_batch, worker=name)
            if done == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stopping.wait(), timeout=WORKER_IDLE_SLEEP)

    try:
        await asyncio.gather(*(lane(i) for i in range(max(1, conf.job_concurrency))))
    finally:
        await dispose()
        log.info("worker stopped")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
