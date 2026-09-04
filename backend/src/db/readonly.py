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
they close instances. This closes the class.

**What is watched.** Whatever the session writes while the read runs: the
flush of the unit of work -- the shape all nine had -- and a bulk
`INSERT`/`UPDATE`/`DELETE` handed straight to the session, which never passes
through a flush. A raw `text("insert ...")` is not seen, and nothing in the
engine writes that way.

**Where it fires** is `EVERSELIFE_READONLY_GUARD`. `raise` by default, which
means every developer's copy and the whole suite: the read stops at the write,
the traceback names the line that did it, and the transaction carries nothing
out. Production sets `warn` (`deploy/compose.yaml`), because the leak worth
catching there is the one no test could reach -- a city of the old world, a
node without a yard -- and turning a player's `look` into "the server failed"
is a worse answer than the extra row. There it goes to the log once, naming the command
and what it wrote -- which is how a leak the suite cannot reach still gets a
name. Not a stack: the flush runs in a greenlet of its own
(`greenlet_spawn`), so a traceback taken there holds SQLAlchemy's frames and
none of the engine's -- the command and the row are the whole of what can
honestly be said.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Iterable
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


#: What has already been said. A leak on the hottest command in the game would
#: otherwise be a line per look in the production log. One line per shape per
#: process, and a second shape from the same command is a second leak rather
#: than a repetition of the first.
_said: set[tuple[str, tuple[str, ...]]] = set()


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
    #: read's.
    brought = {id(row) for row in (*db.new, *db.dirty, *db.deleted)}

    def wrote(rows: Iterable[str]) -> None:
        written = tuple(sorted(set(rows)))
        if not written:
            return
        if chosen == "raise":
            raise ReadWrote(f"{what} is declared readonly and wrote: {', '.join(written)}")
        if (what, written) not in _said:
            _said.add((what, written))
            log.warning("readonly command %s wrote %s", what, ", ".join(written))

    def on_flush(session: Session, context: Any) -> None:
        wrote(
            type(row).__name__
            for row in (*session.new, *session.dirty, *session.deleted)
            if id(row) not in brought
        )

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
