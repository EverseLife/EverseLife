# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The language of a session, over the wire (D-249, D-251 wave III).

The framework is tested in `test_i18n.py`; this is the part a player touches:
the account remembers a language, the greeting says which, a refusal carries
its key and arguments beside the sentence, and the words themselves can be
fetched by whoever wants to say the same thing.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src import i18n
from src.api.app import create_app
from src.engine import world
from src.models import Account
from tests.conftest import TEST_DATABASE_URL

PASSWORD = "kirka-i-krep"


async def _prepare() -> dict:
    """One node and one account on it -- the least a session needs to exist."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        stamp = uuid.uuid4().hex[:6]
        node = await world.create_node(db, f"terra.locale.{stamp}", "Двор", area_m2=100)
        identity = await world.create_identity(
            db, f"Локаль-{stamp}", email=f"locale-{stamp}@example.com", password=PASSWORD
        )
        await world.print_body(db, identity, node)
        account = await db.get(Account, identity.account_id)
        ready = {"name": identity.name, "email": account.email}
        await db.commit()
    await engine.dispose()
    return ready


@pytest.fixture
def player(loaded) -> dict:
    try:
        return asyncio.run(_prepare())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"нет тестовой базы ({TEST_DATABASE_URL}): {exc}")


@pytest.fixture
def client(player, monkeypatch):
    """A live server on the test database.

    The engine cell is reset around it for the same reason `test_session.py`
    does: the settings are cached for the process, and a copy configured by
    one test must not be the copy the next one reads.
    """
    monkeypatch.setenv("EVERSELIFE_DATABASE_URL", TEST_DATABASE_URL)

    from src.db import base as db_base
    from src.settings import settings

    settings.cache_clear()
    db_base._engine = None
    db_base._sessionmaker = None

    with TestClient(create_app()) as made:
        yield made

    settings.cache_clear()
    db_base._engine = None
    db_base._sessionmaker = None


def _hello(ws, player: dict) -> dict:
    ws.send_json({"cmd": "hello", "email": player["email"], "password": PASSWORD})
    return ws.receive_json()


def test_the_greeting_says_which_language_it_answers_in(client, player) -> None:
    """The client cannot derive it from anything else sent, so it is said (D-225)."""
    with client.websocket_connect("/session/ws") as ws:
        assert _hello(ws, player)["locale"] == i18n.DEFAULT_LOCALE


def test_a_language_is_chosen_and_remembered(client, player) -> None:
    """Chosen on the account, not on the body: printing a new one must not
    switch the world back into a language nobody reads."""
    with client.websocket_connect("/session/ws") as ws:
        _hello(ws, player)

        ws.send_json({"cmd": "account.locale", "locale": "ru"})
        assert ws.receive_json()["locale"] == "ru"

        #: `ru-RU` is the same language: the two doors into one setting -- the
        #: greeting and this command -- must not disagree about that.
        ws.send_json({"cmd": "account.locale", "locale": "ru-RU"})
        assert ws.receive_json()["locale"] == "ru"

        #: A language we do not serve is refused, not silently replaced.
        ws.send_json({"cmd": "account.locale", "locale": "kl"})
        refused = ws.receive_json()
        assert refused["code"] == "session-locale-unknown"
        assert refused["args"] == {"locale": "kl"}


def test_a_refusal_carries_the_sentence_the_key_and_the_numbers(client, player) -> None:
    """Three fields where there used to be one: words for the player, a code
    for whoever acts on it (D-224), and the arguments it was built from."""
    with client.websocket_connect("/session/ws") as ws:
        _hello(ws, player)

        ws.send_json({"cmd": "нет-такой-команды"})
        answer = ws.receive_json()
        assert answer["code"] == "session-command-unknown"
        assert answer["args"] == {"cmd": "нет-такой-команды"}
        #: The sentence is rendered, not the key: a player reading `code` would
        #: mean the whole wave did nothing.
        assert answer["refused"] == "нет такой команды: нет-такой-команды"


def test_a_refusal_with_no_numbers_stays_two_fields_wide(client) -> None:
    """`args` is dropped when empty rather than sent as `{}` (D-225 in spirit)."""
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"cmd": "look"})
        answer = ws.receive_json()
        assert answer["code"] == "session-need-hello"
        assert "args" not in answer


def test_the_account_keeps_the_language_across_logins(client, player) -> None:
    """Read back off the account at the next `hello`, not held in the socket."""
    with client.websocket_connect("/session/ws") as ws:
        _hello(ws, player)
        ws.send_json({"cmd": "account.locale", "locale": "ru"})
        ws.receive_json()

    with client.websocket_connect("/session/ws") as ws:
        assert _hello(ws, player)["locale"] == "ru"


def test_the_words_are_served_to_whoever_wants_to_say_them(client) -> None:
    """One file, two runtimes: the client parses exactly what the server renders."""
    answer = client.get(f"/public/i18n/{i18n.DEFAULT_LOCALE}").json()
    assert answer["locale"] == i18n.DEFAULT_LOCALE
    assert answer["locales"] == list(i18n.LOCALES)
    #: Not a fixture of the text -- the point is that it is the real thing.
    assert "session-need-hello" in answer["ftl"]

    #: An unknown language is answered in the default one rather than 404: a
    #: stale client must still get words.
    assert client.get("/public/i18n/kl").json()["locale"] == i18n.DEFAULT_LOCALE


def test_every_function_a_message_calls_is_one_the_bundle_registers(loaded) -> None:
    """A message calling `KIND()` where the bundle has no `KIND` renders the
    call itself into the sentence -- Fluent does not fail, it prints.

    This is the check that catches the two ends drifting apart: the client
    mirrors this same list, and a function added on one side only is exactly
    how a refusal turns into `{KIND()}` on a player's screen.
    """
    from fluent.syntax import FluentParser
    from fluent.syntax import ast as ftl

    known = set(i18n.NAME_FUNCTIONS) | set(i18n.LIST_FUNCTIONS) | {"NUMBER", "DATETIME"}
    called: set[str] = set()
    stack: list = [FluentParser().parse(i18n.current().source(i18n.DEFAULT_LOCALE))]
    while stack:
        node = stack.pop()
        if isinstance(node, ftl.FunctionReference):
            called.add(node.id.name)
        for value in vars(node).values() if hasattr(node, "__dict__") else ():
            if isinstance(value, ftl.BaseNode):
                stack.append(value)
            elif isinstance(value, list):
                stack.extend(item for item in value if isinstance(item, ftl.BaseNode))
    assert called <= known, f"сообщения зовут неизвестные функции: {sorted(called - known)}"
