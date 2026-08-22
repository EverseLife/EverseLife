# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
# ruff: noqa: F811 -- the fixtures are imported, and pytest names them as parameters

"""The server speaks first (D-226): events reach the socket without being asked.

Synchronous throughout, like `test_session`: `TestClient` holds the loop, and
the world is changed from a separate run so the journal's trigger fires from
another connection -- the way the worker would.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.engine import world
from src.models.event import Event
from src.models.identity import Body, Identity
from src.models.world import Node
from tests.conftest import TEST_DATABASE_URL
from tests.test_session import _input, cheap_pow, client, miner  # noqa: F401, F811 -- fixtures


async def _learn(name: str, key: str) -> int:
    """Teach the identity out of band and return the journal row's id."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db, db.begin():
            identity = (
                await db.execute(select(Identity).where(Identity.name == name))
            ).scalar_one()
            await world.learn(db, identity, key)
        async with async_sessionmaker(engine)() as db:
            return (await db.execute(select(func.max(Event.id)))).scalar()
    finally:
        await engine.dispose()


def _sorted(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """Answers carry `id`, events carry `event`; order between them is not promised."""
    answers = [m for m in messages if "id" in m]
    events = [m for m in messages if "event" in m]
    return answers, events


def test_answer_carries_the_command_id(client, miner) -> None:
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"id": 7, **_input(miner)})
        hello = ws.receive_json()
        assert hello["id"] == 7 and hello["hello"] == miner["name"]

        ws.send_json({"id": "a-1", "cmd": "look"})
        assert ws.receive_json()["id"] == "a-1"

        #: Without an id the answer has none either: the old client reads by order.
        ws.send_json({"cmd": "look"})
        assert "id" not in ws.receive_json()


def test_event_arrives_unasked_after_hello_with_since(client, miner) -> None:
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"id": 1, **_input(miner), "since": 0})
        assert ws.receive_json()["id"] == 1

        seq = asyncio.run(_learn(miner["name"], "Каменная кирка"))

        told = ws.receive_json()
        assert told["event"] == "knowledge.learned", told
        assert told["seq"] == seq
        assert told["touches"] == ["knowledge"]
        assert told["key"] == "Каменная кирка"
        assert "id" not in told


def test_without_since_the_socket_stays_silent(client, miner) -> None:
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json(_input(miner))
        ws.receive_json()
        asyncio.run(_learn(miner["name"], "Каменная кирка"))
        #: The next thing read is the answer to the next command, not an event.
        ws.send_json({"cmd": "look"})
        assert "look" in ws.receive_json()


def test_hello_with_since_replays_what_was_missed(client, miner) -> None:
    first = asyncio.run(_learn(miner["name"], "Каменная кирка"))
    second = asyncio.run(_learn(miner["name"], "Верёвка"))
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"id": 1, **_input(miner), "since": first})
        assert ws.receive_json()["id"] == 1
        told = ws.receive_json()
        assert told["event"] == "knowledge.learned" and told["seq"] == second
        assert told["key"] == "Верёвка"


def test_room_hears_that_something_was_said(client, miner) -> None:
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"id": 1, **_input(miner), "since": 0})
        ws.receive_json()
        ws.send_json({"id": 2, "cmd": "chat.say", "text": "Есть кто живой?", "kind": "speech"})
        first = ws.receive_json()
        assert "refused" not in first, first
        answers, events = _sorted([first, ws.receive_json()])
        assert answers and answers[0]["id"] == 2
        assert events == [{"event": "chat.said", "touches": ["chat"]}]


def test_since_must_be_a_number(client, miner) -> None:
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({**_input(miner), "since": "вчера"})
        assert "since" in ws.receive_json()["refused"]


def test_unknown_identity_gets_nothing(client, miner) -> None:
    """A stranger's event is not ours: the actor filter holds."""
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"id": 1, **_input(miner), "since": 0})
        ws.receive_json()
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)

        async def stranger() -> None:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db, db.begin():
                other = await world.create_identity(
                    db,
                    f"Чужой-{uuid.uuid4().hex[:6]}",
                    email=f"other-{uuid.uuid4().hex[:6]}@example.com",
                    password="x" * 8,
                )
                await world.learn(db, other, "Верёвка")
            await engine.dispose()

        asyncio.run(stranger())
        ws.send_json({"id": 2, "cmd": "look"})
        assert ws.receive_json()["id"] == 2


def test_look_is_the_live_part_and_the_rest_has_its_own_commands(client, miner) -> None:
    """Step 2 of 08-session-protocol: what changes rarely is not in `look`."""
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"id": 1, **_input(miner), "since": 0})
        ws.receive_json()
        ws.send_json({"id": 2, "cmd": "look"})
        look = ws.receive_json()["look"]
        slow = {
            "knows",
            "discovered",
            "agrotech",
            "profile",
            "orders",
            "reservations",
            "batches",
            "deeds",
        }
        assert not slow & look.keys(), slow & look.keys()
        assert "shelf" not in look["node"]
        for part in ("body", "node", "inventory", "money", "doings"):
            assert part in look, part

        ws.send_json({"id": 3, "cmd": "knowledge"})
        knowledge = ws.receive_json()["knowledge"]
        assert set(knowledge) == {"knows", "discovered", "agrotech"}

        ws.send_json({"id": 4, "cmd": "orders"})
        orders = ws.receive_json()["orders"]
        assert set(orders) == {"orders", "reservations", "batches"}

        ws.send_json({"id": 5, "cmd": "deeds"})
        assert ws.receive_json()["deeds"] == []

        #: No library in the mine: an empty shelf, not a refusal.
        ws.send_json({"id": 6, "cmd": "shelf"})
        assert ws.receive_json()["shelf"] == []

        ws.send_json({"id": 7, "cmd": "account.profile"})
        assert ws.receive_json()["profile"]["name"] == miner["name"]


def test_learning_reaches_the_knowledge_command_after_its_event(client, miner) -> None:
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"id": 1, **_input(miner), "since": 0})
        ws.receive_json()
        ws.send_json({"id": 2, "cmd": "knowledge"})
        before = ws.receive_json()["knowledge"]["knows"]
        assert "Верёвка" not in before

        asyncio.run(_learn(miner["name"], "Верёвка"))
        told = ws.receive_json()
        assert told["event"] == "knowledge.learned" and "knowledge" in told["touches"]

        ws.send_json({"id": 3, "cmd": "knowledge"})
        assert "Верёвка" in ws.receive_json()["knowledge"]["knows"]


def test_the_room_sees_what_changes_the_place_with_a_name_but_not_the_pocket(client, miner) -> None:
    """A bystander in the node hears a node-visible event with `who` and the
    node's touches; the actor hears it as their own, with the pocket touched."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)

    async def neighbour() -> dict:
        stamp = uuid.uuid4().hex[:6]
        async with async_sessionmaker(engine, expire_on_commit=False)() as db, db.begin():
            mine = (
                await db.execute(select(Identity).where(Identity.name == miner["name"]))
            ).scalar_one()
            body = (await db.execute(select(Body).where(Body.identity_id == mine.id))).scalar_one()
            node = await db.get(Node, body.node_id)
            other = await world.create_identity(
                db, f"Сосед-{stamp}", email=f"next-{stamp}@example.com", password="kirka-i-krep"
            )
            await world.print_body(db, other, node)
            return {"email": f"next-{stamp}@example.com", "name": other.name}

    neighbour_ = asyncio.run(neighbour())
    asyncio.run(engine.dispose())

    with (
        client.websocket_connect("/session/ws") as me,
        client.websocket_connect("/session/ws") as them,
    ):
        me.send_json({"id": 1, **_input(miner), "since": 0})
        them.send_json(
            {
                "id": 1,
                "cmd": "hello",
                "email": neighbour_["email"],
                "password": "kirka-i-krep",
                "since": 0,
            }
        )
        assert "refused" not in me.receive_json()
        assert "refused" not in them.receive_json()
        #: Printing the neighbour happened before their hello: nothing replayed.

        me.send_json({"id": 2, "cmd": "look"})
        rope = next(
            t for t in me.receive_json()["look"]["inventory"] if t["goods"] == "Шахтная крепь"
        )
        me.send_json({"id": 3, "cmd": "ground.drop", "item": rope["id"], "amount": 1})

        mine_ = [me.receive_json(), me.receive_json()]
        answers, own = _sorted(mine_)
        assert answers[0]["id"] == 3 and "refused" not in answers[0], answers
        assert [e["event"] for e in own] == ["item.dropped"]
        assert "who" not in own[0] and "inventory" in own[0]["touches"]

        heard = them.receive_json()
        assert heard["event"] == "item.dropped"
        assert heard["who"] == miner["name"]
        assert heard["touches"] == ["node"]
