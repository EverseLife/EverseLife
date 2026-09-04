# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The command registry (wave 3): names are unique, both handler shapes run,
every command the client sends exists on the server -- and a command that
declared itself a read is held to it (review 2026-08-23, wave 1 item 4).

The reads of the game are swept as a class in `test_reads.py`; what is checked
here is the check itself -- that it catches a write of either shape, that
production reads on and says so in the log, and that a command which never
claimed to be a read is left alone.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

import src.api.session  # noqa: F401 -- registers the commands
from src.api.registry import COMMANDS, Command, Ctx, Refused, command
from src.db.readonly import ReadWrote
from src.models.identity import Account
from src.settings import settings

CLIENT = Path(__file__).resolve().parents[2] / "frontend" / "src"


def test_a_name_registers_once() -> None:
    @command("test.once")
    async def first(ctx: Ctx) -> dict:
        return {}

    with pytest.raises(RuntimeError):

        @command("test.once")
        async def second(ctx: Ctx) -> dict:
            return {}

    del COMMANDS["test.once"]


async def test_both_handler_shapes_are_served(session: AsyncSession) -> None:
    """A real session and not `None`: the readonly half of the pair now runs
    under the guard, and the guard listens to the session it is given."""

    async def old(state: dict, db: object, message: dict) -> dict:
        return {"shape": "state", "id": state["identity_id"]}

    async def new(ctx: Ctx) -> dict:
        return {"shape": "ctx", "id": ctx.identity_id}

    legacy = Command(name="t", handler=old, readonly=False, doc="", takes_ctx=False)
    modern = Command(name="t", handler=new, readonly=True, doc="", takes_ctx=True)
    state = {"identity_id": "who"}
    assert await legacy.run(state, session, {}) == {"shape": "state", "id": "who"}
    assert await modern.run(state, session, {}) == {"shape": "ctx", "id": "who"}


def test_every_command_the_client_sends_exists() -> None:
    """`session.send("x.y")` and `part()` names in the client must be commands
    here; a renamed command would otherwise fail only in the browser."""
    sent: set[str] = set()
    for path in CLIENT.rglob("*.ts*"):
        text = path.read_text(encoding="utf-8")
        sent |= set(re.findall(r'\.send\(\s*"([a-z_.]+)"', text))
        sent |= set(re.findall(r'"([a-z_.]+)"\s*,?\s*\)\s*;?\s*//.*command', text))
    sent -= {"hello", "join"}
    missing = sorted(name for name in sent if name not in COMMANDS)
    assert missing == [], missing


def test_every_command_has_a_doc() -> None:
    """The AI agent's reference is built from the docstrings."""
    undocumented = sorted(name for name, c in COMMANDS.items() if not c.doc)
    assert undocumented == [], undocumented


def _reads(handler) -> Command:
    """A command that says it only reads, whatever its body actually does."""
    return Command(name="test.read", handler=handler, readonly=True, doc="", takes_ctx=False)


async def _writes(state: dict, db: AsyncSession, message: dict) -> dict:
    """The shape all nine leaks had: `session.add` and a flush behind it, on
    the reader's own transaction."""
    row = Account()
    db.add(row)
    await db.flush()
    return {"account": str(row.id)}


async def test_a_read_that_writes_is_stopped(session: AsyncSession, monkeypatch) -> None:
    """It is stopped where it writes, and the message names what it wrote --
    the whole difference between a flag that declares and a flag that checks."""
    monkeypatch.setattr(settings(), "readonly_guard", "raise")
    with pytest.raises(ReadWrote, match="test.read is declared readonly and wrote: Account"):
        await _reads(_writes).run({"identity_id": None}, session, {})


async def test_a_read_that_writes_without_a_flush_is_stopped(
    session: AsyncSession, monkeypatch
) -> None:
    """A statement handed to the session goes to the database without ever
    passing through a flush, so the listener on the unit of work does not see
    it. Nothing reads that way today; the guard would be worth little if the
    first one to do so were invisible."""
    monkeypatch.setattr(settings(), "readonly_guard", "raise")

    async def liar(state: dict, db: AsyncSession, message: dict) -> dict:
        await db.execute(delete(Account).where(Account.id == uuid.uuid4()))
        return {"read": True}

    with pytest.raises(ReadWrote, match="delete account"):
        await _reads(liar).run({"identity_id": None}, session, {})


async def test_in_production_the_read_answers_and_the_write_is_named(
    session: AsyncSession, monkeypatch, caplog
) -> None:
    """`warn` is what the deploy sets. The leak worth catching in production is
    the one no test could furnish -- a city of the old world, a node without a
    yard -- and refusing the player their `look` over it would be a worse
    answer than the extra row. So the read answers, and the log carries the
    name of the command and of what it wrote."""
    monkeypatch.setattr(settings(), "readonly_guard", "warn")
    named = Command(name="test.warn", handler=_writes, readonly=True, doc="", takes_ctx=False)
    with caplog.at_level(logging.WARNING, logger="src.db.readonly"):
        answer = await named.run({"identity_id": None}, session, {})
    assert [record.message for record in caplog.records] == [
        "readonly command test.warn wrote Account"
    ]
    #: The read answered, and what it wrote is in the database: production is
    #: told about the leak, not saved from it. Saving it from the row would
    #: mean rolling back the answer with it.
    assert await session.scalar(
        select(Account.id).where(Account.id == uuid.UUID(answer["account"]))
    )


async def test_a_command_that_never_claimed_to_be_a_read_writes(session: AsyncSession) -> None:
    """The guard is the flag and nothing more: an ordinary command writes as
    it always did, and pays nothing for the listeners it does not get."""
    writer = Command(name="test.write", handler=_writes, readonly=False, doc="", takes_ctx=False)
    answer = await writer.run({"identity_id": None}, session, {})
    assert await session.scalar(
        select(Account.id).where(Account.id == uuid.UUID(answer["account"]))
    )


async def test_a_read_that_writes_without_flushing_is_stopped(
    session: AsyncSession, monkeypatch
) -> None:
    """A row added and left pending reaches the database all the same: the
    commit at the end of `_dispatch` flushes it, and by then the listeners on
    the session are gone. Nine leaks flushed because get-or-create needs the
    id; the tenth need not."""
    monkeypatch.setattr(settings(), "readonly_guard", "raise")

    async def liar(state: dict, db: AsyncSession, message: dict) -> dict:
        db.add(Account())
        return {"read": True}

    with pytest.raises(ReadWrote, match="test.read is declared readonly and wrote: Account"):
        await _reads(liar).run({"identity_id": None}, session, {})


async def test_a_read_that_changes_a_loaded_row_is_stopped(
    session: AsyncSession, monkeypatch
) -> None:
    """The cheapest form of them all -- `body.seen_at = now` on a row the read
    itself loaded. No `add`, no flush, and the commit writes it."""
    monkeypatch.setattr(settings(), "readonly_guard", "raise")
    row = Account(locale="ru")
    session.add(row)
    await session.flush()

    async def liar(state: dict, db: AsyncSession, message: dict) -> dict:
        (await db.get(Account, row.id)).locale = "en"
        return {"read": True}

    with pytest.raises(ReadWrote, match="wrote: Account"):
        await _reads(liar).run({"identity_id": None}, session, {})


async def test_writing_back_what_was_already_there_is_not_a_write(
    session: AsyncSession, monkeypatch
) -> None:
    """SQLAlchemy calls a row dirty when an attribute is **set**, changed or
    not, and committing such a row writes nothing. Stopping the hottest
    command in the game over `locale = locale` would be a defect of its own,
    so the check asks `is_modified` and not `dirty`."""
    monkeypatch.setattr(settings(), "readonly_guard", "raise")
    row = Account(locale="ru")
    session.add(row)
    await session.flush()

    async def reads(state: dict, db: AsyncSession, message: dict) -> dict:
        found = await db.get(Account, row.id)
        found.locale = found.locale
        return {"locale": found.locale}

    assert await _reads(reads).run({"identity_id": None}, session, {}) == {"locale": "ru"}


async def test_a_refusal_takes_its_leftovers_with_it(session: AsyncSession, monkeypatch) -> None:
    """A read that refuses is not held to the leftovers: its transaction is
    rolled back whole (`api/session._dispatch`), so nothing pending on it ever
    reaches the database. The refusal must arrive as itself -- the player is
    owed the reason, not "the server failed"."""
    monkeypatch.setattr(settings(), "readonly_guard", "raise")

    async def refuses(state: dict, db: AsyncSession, message: dict) -> dict:
        db.add(Account())
        raise Refused(key="cmd-no-live-body")

    with pytest.raises(Refused):
        await _reads(refuses).run({"identity_id": None}, session, {})


async def test_what_the_caller_brought_is_not_the_reads_doing(
    session: AsyncSession, monkeypatch
) -> None:
    """A test that runs a command on the session it built its world with
    brings its own unflushed rows along, and the first query inside the read
    flushes them. They are the caller's, and the read is not stopped over
    them -- in production the session is the command's own and empty."""
    monkeypatch.setattr(settings(), "readonly_guard", "raise")
    session.add(Account())

    async def reads(state: dict, db: AsyncSession, message: dict) -> dict:
        #: The query autoflushes what the caller left pending.
        return {"accounts": await db.scalar(select(func.count()).select_from(Account))}

    assert (await _reads(reads).run({"identity_id": None}, session, {}))["accounts"] >= 1


async def test_off_leaves_the_read_alone(session: AsyncSession, monkeypatch, caplog) -> None:
    """`off` is neither check nor log: the copy that wants neither pays for
    neither, and the write goes through as it did before any of this."""
    monkeypatch.setattr(settings(), "readonly_guard", "off")
    with caplog.at_level(logging.WARNING, logger="src.db.readonly"):
        answer = await _reads(_writes).run({"identity_id": None}, session, {})
    assert caplog.records == []
    assert await session.scalar(
        select(Account.id).where(Account.id == uuid.UUID(answer["account"]))
    )


async def test_the_same_leak_is_named_once(session: AsyncSession, monkeypatch, caplog) -> None:
    """One line per leak per `SAID_FOR`, not one per read: a leak on `look`
    would otherwise be a line per look in the production log."""
    monkeypatch.setattr(settings(), "readonly_guard", "warn")
    named = Command(name="test.twice", handler=_writes, readonly=True, doc="", takes_ctx=False)
    with caplog.at_level(logging.WARNING, logger="src.db.readonly"):
        await named.run({"identity_id": None}, session, {})
        await named.run({"identity_id": None}, session, {})
    assert [record.message for record in caplog.records] == [
        "readonly command test.twice wrote Account"
    ]
