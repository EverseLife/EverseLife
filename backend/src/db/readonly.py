# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""`readonly=True` held to its word: a read that writes is caught, not read about.

The flag on `@command` was a declaration and nothing else (`api/registry.py`):
the transaction it opened was an ordinary read-write one, so an INSERT inside
a read went out with the reader's own commit. Nine of those were found between
2026-08-23 and 2026-09-04 (review 2026-08-23, wave 1 item 4), every one of
them by somebody reading the code, and every one the same shape -- a read asks
a get-or-create helper for something it could have counted without one:
`node_container` gives the node a yard, `account_for` opens an account,
`city_channel` gives the city a voice, `session_container` gives the face a
hold. The eighth fired in production once per city and went unnoticed for
weeks; the ninth could not fire outside the tests at all. Found one at a time,
they close instances; the point of a check is that the next one fails where
it is written, on whoever wrote it, instead of waiting to be read.

**What is watched.** Whatever the session would write because of the read.
The flush of the unit of work -- the shape all nine had. A bulk
`INSERT`/`UPDATE`/`DELETE` handed straight to the session, which never passes
through a flush. And what the read leaves *pending* and never flushes at all
-- a bare `session.add`, or a plain `body.seen_at = now` on a loaded row:
those go out with the flush `db.begin()` does on its way to the commit, long
after the listeners are gone, so they are counted when the block ends instead.
A raw `text("insert ...")` is not seen, and nothing in the engine writes that
way.

**Where it fires** is `EVERSELIFE_READONLY_GUARD`. `raise` by default, which
means every developer's copy and the whole suite: the write stops the read and
the transaction carries nothing out. A write that flushed is stopped at the
flush, so the traceback runs down to the engine call that did it; one merely
left pending is stopped when the block ends, and names the command instead --
the price of catching a write nobody flushed.

Production sets `warn` (`deploy/compose.yaml`), because the leak worth
catching there is the one no test could reach -- a city of the old world, a
node without a yard -- and turning a player's `look` into "the server failed"
is a worse answer than the extra row. There it goes to the log, the command
and what it wrote, once per shape per `SAID_FOR` -- which is how a leak the
suite cannot reach still gets a name. Not a stack: the flush runs in a
greenlet of its own (`greenlet_spawn`), and a traceback taken inside it holds
SQLAlchemy's frames and none of the engine's.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any, Literal

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session

from src.settings import settings

log = logging.getLogger(__name__)

#: Off, in the log, or in the face. Written out in `settings.Settings` as well:
#: settings may not import the database layer, which imports settings.
type Guard = Literal["off", "warn", "raise"]


class ReadWrote(RuntimeError):
    """A command that declared itself a read has written.

    Deliberately not a `Refusal`: no rule of the game was broken, the code was.
    The socket answers "the server failed" and logs the whole of it -- which on
    a developer's copy is the right answer, and the reason `raise` is the default.
    """


#: What has already been said, and when. A leak on the hottest command in the
#: game would otherwise be a line per look in the production log.
_said: dict[tuple[str, tuple[str, ...]], float] = {}

#: How long one leak stays said. Not "once per process": two leaks of the same
#: command may write rows of the same class -- `node_container` and
#: `session_container` both make a `Container`, and `look` reaches both -- and
#: a key that never expires would name the first and hide the second until a
#: restart. An hour is one line an hour from a leak on `look`, and no leak
#: left unnamed for longer than that.
SAID_FOR = 3600.0


def _written(session: Session, brought: set[int]) -> Iterator[str]:
    """What this session would write, by row class, minus what the caller
    brought in pending.

    `dirty` is asked through `is_modified` rather than taken as it stands:
    SQLAlchemy counts an attribute **set** as dirty whether or not the value
    changed, and committing such a row writes nothing. `state = state` inside
    a read is not a write, and stopping the hottest command in the game over
    one would be a defect of its own.
    """
    rows = (
        *session.new,
        *(row for row in session.dirty if session.is_modified(row)),
        *session.deleted,
    )
    return (type(row).__name__ for row in rows if id(row) not in brought)


@contextlib.asynccontextmanager
async def writes_forbidden(
    db: AsyncSession, what: str, *, mode: Guard | None = None
) -> AsyncIterator[None]:
    """Hold `what` -- a command name, or whatever the caller is proving -- to
    writing nothing for as long as the block runs."""
    chosen: Guard = settings().readonly_guard if mode is None else mode
    if chosen == "off":
        yield
        return
    #: What the caller brought in pending is not this read's doing. In
    #: production a command starts on a session of its own with nothing on it
    #: (`api/session._dispatch`); the case this is for is a test that runs a
    #: command on the session it built its world with -- its own unflushed rows
    #: would be flushed by the first query inside the read and counted as the
    #: read's. The list is held and not only the addresses taken from it: a row
    #: collected on the way would free its address for the very row this is
    #: looking for.
    pending = [*db.new, *db.dirty, *db.deleted]
    brought = {id(row) for row in pending}

    def wrote(rows: Iterable[str]) -> None:
        written = tuple(sorted(set(rows)))
        if not written:
            return
        if chosen == "raise":
            raise ReadWrote(f"{what} is declared readonly and wrote: {', '.join(written)}")
        now = time.monotonic()
        said = _said.get((what, written))
        if said is None or now - said > SAID_FOR:
            _said[(what, written)] = now
            log.warning("readonly command %s wrote %s", what, ", ".join(written))

    def on_flush(session: Session, context: Any) -> None:
        wrote(_written(session, brought))

    def on_execute(state: ORMExecuteState) -> None:
        #: A statement handed to the session writes without a flush, so the
        #: listener above never sees it. Access lists, chat and foraging are
        #: written that way today -- none of them from a read, and that is
        #: exactly what this keeps true.
        if not (state.is_insert or state.is_update or state.is_delete):
            return
        table = getattr(state.statement, "table", None)
        wrote([f"{state.statement.__visit_name__} {'?' if table is None else table.name}"])

    event.listen(db.sync_session, "after_flush", on_flush)
    event.listen(db.sync_session, "do_orm_execute", on_execute)
    try:
        yield
    finally:
        event.remove(db.sync_session, "after_flush", on_flush)
        event.remove(db.sync_session, "do_orm_execute", on_execute)
    #: What the read leaves behind unflushed. It reaches the database all the
    #: same -- `db.begin()` flushes on its way to the commit -- and by then the
    #: listeners are gone, so it is counted here instead. After the `finally`
    #: on purpose: this line runs only when the read returned an answer, and a
    #: read that refused took its transaction down with it, leftovers included.
    wrote(_written(db.sync_session, brought))
