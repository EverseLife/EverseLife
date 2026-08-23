# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Who the alpha's widget opens for, through the socket itself (D-229).

`test_alpha.py` checks what the levers do; this checks who may pull them, and
it has to go end to end -- the gate is not in the engine at all. It reads
settings, and settings are a property of the copy being run.

Two things are asserted about the closed door: the command refuses, and it
refuses in the words an unknown command gets. A player who guessed the name
must not learn from the refusal that the name was right.

Synchronous throughout, as in `test_session.py`: `TestClient` holds its own
event loop, and an async fixture beside it is a way to get two.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.api.app import create_app
from src.engine import world
from src.models import Account
from tests.conftest import TEST_DATABASE_URL, reset

PASSWORD = "tern-terra-2026"


async def _prepare_world() -> dict:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    await reset(engine)

    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        node = await world.create_node(db, "terra.yard", "Двор", area_m2=100)
        stamp = uuid.uuid4().hex[:6]
        identity = await world.create_identity(
            db, f"Тэрн-{stamp}", email=f"tern-{stamp}@example.com", password=PASSWORD
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
        return asyncio.run(_prepare_world())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"нет тестовой базы ({TEST_DATABASE_URL}): {exc}")


def _client(monkeypatch, admins: str) -> TestClient:
    monkeypatch.setenv("EVERSELIFE_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("EVERSELIFE_ADMINS", admins)

    from src.db import base as db_base
    from src.settings import settings

    settings.cache_clear()
    db_base._engine = None
    db_base._sessionmaker = None
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _forget_settings():
    """The settings are cached for the process: a copy configured by one test
    must not be the copy the next one reads."""
    yield
    from src.db import base as db_base
    from src.settings import settings

    settings.cache_clear()
    db_base._engine = None
    db_base._sessionmaker = None


def _hello(player: dict) -> dict:
    return {"cmd": "hello", "email": player["email"], "password": PASSWORD}


def test_closed_by_default(player, monkeypatch) -> None:
    """An empty list is the default on every copy: the widget is for nobody
    until a line of the environment says otherwise."""
    with _client(monkeypatch, "[]") as client, client.websocket_connect("/session/ws") as ws:
        ws.send_json(_hello(player))
        hello = ws.receive_json()
        assert hello["hello"] == player["name"]
        assert "admin" not in hello, "ключ доступа уехал тому, у кого доступа нет"

        ws.send_json({"cmd": "alpha.spawn", "goods": "Железная руда", "amount": 1})
        answer = ws.receive_json()
        #: The same words an unknown command gets: guessing the name teaches
        #: nothing about whether it exists.
        assert answer["refused"] == "нет такой команды: alpha.spawn"


def test_a_name_off_the_list_is_refused(player, monkeypatch) -> None:
    """Somebody else's name on the list is not this player's access."""
    with (
        _client(monkeypatch, '["Хём"]') as client,
        client.websocket_connect("/session/ws") as ws,
    ):
        ws.send_json(_hello(player))
        assert "admin" not in ws.receive_json()

        ws.send_json({"cmd": "alpha.hurry"})
        assert ws.receive_json()["refused"] == "нет такой команды: alpha.hurry"


def test_an_admin_name_cannot_be_registered(player, monkeypatch) -> None:
    """The list is written into a compose file, so the name is public and
    guessable. On a copy where the seed has not made that identity yet, the
    first comer would otherwise register straight into the widget.

    The refusal is word for word the one a taken name gets: guessing right
    must teach nothing about whether the guess was right.
    """
    import json

    wanted = "Тэрн"
    admins = json.dumps([wanted], ensure_ascii=False)
    with _client(monkeypatch, admins) as client, client.websocket_connect("/session/ws") as ws:
        ws.send_json(
            {
                "cmd": "join",
                "email": f"someone-{uuid.uuid4().hex[:6]}@example.com",
                "password": "kirka-i-krep",
                "name": wanted,
                "line": "human",
            }
        )
        answer = ws.receive_json()
        assert answer["refused"] == f"имя {wanted!r} уже занято: имя сменить нельзя"


def test_the_named_one_gets_the_widget(player, monkeypatch) -> None:
    """On the list -- the greeting carries the key and the levers work."""
    import json

    admins = json.dumps([player["name"]], ensure_ascii=False)
    with _client(monkeypatch, admins) as client, client.websocket_connect("/session/ws") as ws:
        ws.send_json(_hello(player))
        assert ws.receive_json()["admin"] is True

        ws.send_json({"cmd": "alpha.spawn", "goods": "Железная руда", "amount": 3})
        made = ws.receive_json()
        assert made["spawned"] == "Железная руда"
        #: In pieces, as asked -- not in the internal integer units the row holds.
        assert made["amount"] == 3

        #: Nothing is running: an honest empty answer rather than a refusal.
        ws.send_json({"cmd": "alpha.hurry"})
        assert ws.receive_json()["hurried"] == []
