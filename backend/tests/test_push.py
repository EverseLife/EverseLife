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
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
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

        seq = asyncio.run(_learn(miner["name"], "stone_pickaxe"))

        told = ws.receive_json()
        assert told["event"] == "knowledge.learned", told
        assert told["seq"] == seq
        assert told["touches"] == ["knowledge"]
        assert told["key"] == "stone_pickaxe"
        assert "id" not in told


def test_without_since_the_socket_stays_silent(client, miner) -> None:
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json(_input(miner))
        ws.receive_json()
        asyncio.run(_learn(miner["name"], "stone_pickaxe"))
        #: The next thing read is the answer to the next command, not an event.
        ws.send_json({"cmd": "look"})
        assert "look" in ws.receive_json()


def test_hello_with_since_replays_what_was_missed(client, miner) -> None:
    first = asyncio.run(_learn(miner["name"], "stone_pickaxe"))
    second = asyncio.run(_learn(miner["name"], "rope"))
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"id": 1, **_input(miner), "since": first})
        assert ws.receive_json()["id"] == 1
        told = ws.receive_json()
        assert told["event"] == "knowledge.learned" and told["seq"] == second
        assert told["key"] == "rope"


def test_room_hears_that_something_was_said(client, miner) -> None:
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"id": 1, **_input(miner), "since": 0})
        ws.receive_json()
        ws.send_json({"id": 2, "cmd": "chat.say", "text": "Есть кто живой?", "kind": "speech"})
        first = ws.receive_json()
        assert "refused" not in first, first
        answers, events = _sorted([first, ws.receive_json()])
        assert answers and answers[0]["id"] == 2
        assert len(events) == 1 and events[0]["event"] == "chat.said"
        line = events[0]["line"]
        #: The line itself rides along (wave 2): the room shows it without asking.
        assert line["text"] == "Есть кто живой?" and line["who"] == miner["name"]
        assert line["kind"] == "speech" and "overheard" not in line


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
                await world.learn(db, other, "rope")
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
        assert set(knowledge) == {"knows", "discovered", "agrotech", "pioneers"}

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


async def _discover(name: str, key: str, when: datetime) -> None:
    """Mark an invention out of band, timed by hand: the pioneer is the earliest."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db, db.begin():
            identity = (
                await db.execute(select(Identity).where(Identity.name == name))
            ).scalar_one()
            learned = await world.learn(db, identity, key, discovered=True)
            assert learned is not None
            learned.acquired_at = when
    finally:
        await engine.dispose()


def test_pioneer_is_the_earliest_discoverer_and_rides_knowledge(client, miner) -> None:
    """The first discoverer's name per known recipe (D-064, D-259): the
    earliest `discovered` row wins, founding recipes carry no name at all."""
    stamp = uuid.uuid4().hex[:6]
    first, second = f"Ранний-{stamp}", f"Поздний-{stamp}"

    async def strangers() -> None:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db, db.begin():
                for name in (first, second):
                    await world.create_identity(
                        db,
                        name,
                        email=f"{uuid.uuid4().hex[:8]}@example.com",
                        password="x" * 8,
                    )
        finally:
            await engine.dispose()

    asyncio.run(strangers())
    #: Both opened the same recipe by experiment; the earlier one is the pioneer.
    asyncio.run(_discover(second, "rope", datetime(2026, 9, 1, 12, tzinfo=UTC)))
    asyncio.run(_discover(first, "rope", datetime(2026, 9, 1, 9, tzinfo=UTC)))
    #: The miner merely knows it -- learned ready-made, no discovery of their own.
    asyncio.run(_learn(miner["name"], "rope"))
    asyncio.run(_learn(miner["name"], "stone_pickaxe"))

    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"id": 1, **_input(miner)})
        ws.receive_json()
        ws.send_json({"id": 2, "cmd": "knowledge"})
        knowledge = ws.receive_json()["knowledge"]
        assert "rope" in knowledge["knows"]
        assert "rope" not in knowledge["discovered"]
        #: One name per key, the earliest; the undiscovered recipe has none.
        assert knowledge["pioneers"] == {"rope": first}


def test_learning_reaches_the_knowledge_command_after_its_event(client, miner) -> None:
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"id": 1, **_input(miner), "since": 0})
        ws.receive_json()
        ws.send_json({"id": 2, "cmd": "knowledge"})
        before = ws.receive_json()["knowledge"]["knows"]
        assert "rope" not in before

        asyncio.run(_learn(miner["name"], "rope"))
        told = ws.receive_json()
        assert told["event"] == "knowledge.learned" and "knowledge" in told["touches"]

        ws.send_json({"id": 3, "cmd": "knowledge"})
        assert "rope" in ws.receive_json()["knowledge"]["knows"]


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
            t for t in me.receive_json()["look"]["inventory"] if t["goods"] == "shaft_support"
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


def _row(kind: str, *, actor=None, node=None, **payload):
    from src.models.event import Event

    return Event(kind=kind, actor_identity_id=actor, node_id=node, payload=payload)


def test_parties_are_named_by_identity_id_keys() -> None:
    """Every `*_identity_id` in the payload is a party; a name is not."""
    from src.api import push

    actor, whom = uuid.uuid4(), uuid.uuid4()
    row = _row("city.office_appointed", actor=actor, whom="Иван", whom_identity_id=str(whom))
    assert push._parties(row) == {actor, whom}
    assert push._parties(_row("justice.case_judged", against="Пётр")) == set()


def test_city_affairs_reach_citizens_and_the_sink_follows_the_convict() -> None:
    from src.api import push

    city = uuid.uuid4()
    assert push._city_of(_row("city.law_set", city_id=str(city))) == city
    assert push._city_of(_row("market.trade", city_id=str(city))) is None

    sink = push.Sink(send_raw=None, node_id=uuid.uuid4())
    cell = uuid.uuid4()
    push._follow(sink, _row("justice.sanction_applied", cell_node_id=str(cell)))
    assert sink.node_id == cell
    found = uuid.uuid4()
    push._follow(sink, _row("explore.found", node=found))
    assert sink.node_id == found


def test_an_addressed_event_reaches_the_party_not_the_actor_alone(client, miner) -> None:
    """Citizenship granted by a clerk: the new citizen is named by id in the
    payload and hears it as their own, with the city's touches."""
    from src.engine import city as town
    from src.models.world import Layer

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)

    async def enroll() -> None:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db, db.begin():
            from src.constants import current_catalog

            mine = (
                await db.execute(select(Identity).where(Identity.name == miner["name"]))
            ).scalar_one()
            stamp = uuid.uuid4().hex[:6]
            delegate = await world.create_node(
                db, f"terra.city.{stamp}", "Город", area_m2=1, layer=Layer.PLANET
            )
            city = await town.found(db, current_catalog(), delegate, f"Город-{stamp}")
            clerk = await world.create_identity(db, f"Писарь-{stamp}")
            await town._enroll(db, city, mine.id, why="тест", by=clerk.id)
        await engine.dispose()

    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"id": 1, **_input(miner), "since": 0})
        ws.receive_json()
        asyncio.run(enroll())
        #: Founding and enrolment commit together, and by delivery the miner
        #: is a citizen already: `city.founded` reaches them as a member first.
        told = ws.receive_json()
        if told["event"] == "city.founded":
            assert told["touches"] == ["city"]
            told = ws.receive_json()
        assert told["event"] == "city.citizenship_granted", told
        assert told["touches"] == ["city"]
        assert told["who"] != miner["name"], "назвавший — писарь, не новый гражданин"


def test_every_refusal_reaches_the_player_as_a_refusal(client, miner) -> None:
    """`LedgerError` was not in the socket loop's list and went out as "the
    server failed" (review 2026-08-23). Now every engine refusal descends
    from `Refusal` and is answered as one -- here a transfer of money the
    identity does not have."""
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"id": 1, **_input(miner)})
        ws.receive_json()
        ws.send_json({"id": 2, "cmd": "finance.transfer", "to": miner["name"], "amount": 999999})
        answer = ws.receive_json()
        assert "refused" in answer and "не справился" not in answer["refused"], answer


def test_all_engine_errors_descend_from_refusal() -> None:
    import importlib
    import inspect
    import pkgutil

    import src.engine as engine
    from src.engine.errors import Refusal
    from src.engine.jobs import UnknownJobKind

    strays = []
    for info in pkgutil.iter_modules(engine.__path__):
        module = importlib.import_module(f"src.engine.{info.name}")
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module.__name__ or not issubclass(cls, Exception):
                continue
            if cls is UnknownJobKind or issubclass(cls, Refusal):
                continue
            strays.append(f"{module.__name__}.{name}")
    assert strays == [], strays


async def test_pump_delivers_a_late_committing_row_exactly_once(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """A row takes its id before a later one that commits ahead of it (review
    2026-08-23): the later one is delivered, the straggler commits below the
    old high-water, and it must still arrive -- exactly once."""
    from sqlalchemy import func, select

    from src.api import push

    #: Patched where the name is looked up (the push split): `pump` binds
    #: `session_factory` into its own globals, so assigning on the door
    #: would reach nobody.
    from src.api.push import pump as push_pump
    from src.engine import events
    from src.models.event import Event, EventKind

    real_factory = push_pump.session_factory
    push_pump.session_factory = lambda: factory  # type: ignore[assignment]
    got: list[int] = []

    async def collect(message: dict) -> None:
        if "seq" in message:
            got.append(message["seq"])

    who = uuid.uuid4()
    sink = push.Sink(send_raw=collect, listening=True, identity_id=who)
    hub = push.Hub()
    sink.send = sink.send_raw  # type: ignore[method-assign]
    hub.sinks.add(sink)
    hub.dirty = True
    try:
        async with factory() as db:
            hub._last_id = (await db.execute(select(func.max(Event.id)))).scalar() or 0

        #: A takes an id and holds its transaction open.
        a = factory()
        await a.__aenter__()
        trans = await a.begin()
        straggler = await events.record(a, EventKind.KNOWLEDGE_LEARNED, actor_identity_id=who)
        await a.flush()

        #: B commits a later id first.
        async with factory() as b, b.begin():
            later = await events.record(b, EventKind.KNOWLEDGE_LEARNED, actor_identity_id=who)

        assert straggler.id < later.id

        await hub.pump()
        assert got == [later.id], "виден только закоммиченный"

        await trans.commit()
        await a.__aexit__(None, None, None)
        await hub.pump()

        assert sorted(got) == [straggler.id, later.id]
        assert got.count(straggler.id) == 1, "опоздавшая строка обязана дойти ровно раз"
    finally:
        push_pump.session_factory = real_factory


async def test_the_watermark_adopts_the_journal_it_reads(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    """One process may serve one journal after another -- another database,
    a restore from a backup. The mark belongs to the journal it reads: kept
    higher, the hub would deliver nothing until the new journal grew past it
    (this is what hung the suite on a fresh database)."""
    from sqlalchemy import func, select

    from src.api import push
    from src.engine import events
    from src.models.event import Event, EventKind

    await events.record(session, EventKind.TICK_RAN, kind_of_tick="test")
    await session.commit()

    #: Patched where the name is looked up (the push split): `pump` binds
    #: `session_factory` into its own globals, so assigning on the door
    #: would reach nobody.
    from src.api.push import pump as push_pump

    real_factory = push_pump.session_factory
    push_pump.session_factory = lambda: factory  # type: ignore[assignment]
    try:
        hub = push.Hub()
        #: A mark left by the journal served before this one.
        hub._last_id = 10**9
        async with factory() as db:
            await hub._settle(db)
            here = (await db.execute(select(func.max(Event.id)))).scalar() or 0
        assert hub._last_id == here, "отметка обязана принадлежать читаемому журналу"
    finally:
        push_pump.session_factory = real_factory
