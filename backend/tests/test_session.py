"""End-to-end check: a full mining session through the client session.

The main thing here is the last check. The hidden number must not leak out
**in any reply**, not only the one we thought of.

The test is synchronous throughout: `TestClient` holds its own event loop,
and preparing the world with an async fixture next to it is a sure way to get
two loops and irreproducible failures. The world is prepared in a separate run.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api.app import create_app
from src.constants import HOLDER, Constants
from src.constants import registry as R
from src.engine import pow as device
from src.engine import world
from src.models import Account, Base
from tests.conftest import TEST_DATABASE_URL


async def _prepare_world() -> dict:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        node = await world.create_node(db, "terra.mine", "Забой", area_m2=100)
        vein = await world.create_vein(db, node, "Железная руда", richness=60, remaining=100_000)
        stamp = uuid.uuid4().hex[:6]
        identity = await world.create_identity(
            db, f"Тэрн-{stamp}", email=f"tern-{stamp}@example.com", password="kirka-i-krep"
        )
        body = await world.print_body(db, identity, node)
        bag = await world.body_container(db, body)
        await world.grant_item(db, bag, "Шахтная крепь", amount=5, origin="сценарий теста")
        account = await db.get(Account, identity.account_id)
        ready_ = {
            "name": identity.name,
            "email": account.email,
            "password": "kirka-i-krep",
            "vein": str(vein.id),
            "account": account.id,
        }
        await db.commit()

    await engine.dispose()
    return ready_


@pytest.fixture
def miner(loaded) -> dict:
    try:
        return asyncio.run(_prepare_world())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"нет тестовой базы ({TEST_DATABASE_URL}): {exc}")


@pytest.fixture
def cheap_pow(constants: Constants) -> Constants:
    """A device fee the test suite can afford -- via hot override."""
    cheap = constants.with_overrides({"pow.memory_per_session": 8, "pow.argon_iterations": 1})
    HOLDER.set(cheap)
    try:
        yield cheap
    finally:
        HOLDER.set(constants)


@pytest.fixture
def client(miner, monkeypatch):
    monkeypatch.setenv("OCTOVERSE_DATABASE_URL", TEST_DATABASE_URL)

    from src.db import base as db_base
    from src.settings import settings

    settings.cache_clear()
    db_base._engine = None
    db_base._sessionmaker = None

    with TestClient(create_app()) as test_client:
        yield test_client

    settings.cache_clear()
    db_base._engine = None
    db_base._sessionmaker = None


def _input(miner: dict) -> dict:
    """Identification by email and password (D-187): there is no login name any more."""
    return {"cmd": "hello", "email": miner["email"], "password": miner["password"]}


def _face(ws, miner: dict, cheap: Constants) -> dict:
    ws.send_json({"cmd": "pow.challenge"})
    task = ws.receive_json()
    answer = device.solve(cheap, miner["account"], bytes.fromhex(task["nonce"]))
    ws.send_json(
        {
            "cmd": "mine.start",
            "vein": miner["vein"],
            "challenge": task["challenge"],
            "answer": answer.hex(),
        }
    )
    return ws.receive_json()


def test_full_mining_session(client, miner, cheap_pow, constants: Constants) -> None:
    answers = []
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json(_input(miner))
        hello = ws.receive_json()
        answers.append(hello)
        assert hello["hello"] == miner["name"]
        assert hello["body"]

        answers.append(_face(ws, miner, cheap_pow))
        assert answers[-1]["sign"], "признак идёт строкой, а не числом"
        assert answers[-1]["mined"] == 0

        for _ in range(4):
            ws.send_json({"cmd": "mine.swing"})
            answers.append(ws.receive_json())

        assert answers[-1]["mined"] > 0
        assert answers[-1]["swings"] == 4
        assert answers[-1]["stamina"] < constants[R.BODY_STAMINA_MAX]

        ws.send_json({"cmd": "mine.timber"})
        answers.append(ws.receive_json())
        assert answers[-1]["timbers"] == 1

        ws.send_json({"cmd": "mine.pace", "pace": "fast"})
        answers.append(ws.receive_json())
        assert answers[-1]["pace"] == "fast"

        ws.send_json({"cmd": "mine.leave"})
        care = ws.receive_json()
        answers.append(care)
        assert care["haul"] > 0

    #: Only `Sight` fields plus the session name go out. Nothing from which
    #: roof stability could be derived is in the replies -- and that is checked
    #: over everything sent to the client, not one field.
    allowed_ = {
        "sign", "mined", "swings", "timbers", "stamina", "pace", "state", "session",
        "hello", "body", "node", "constants", "challenge", "nonce", "left", "haul",
        #: The session token is identification, not game (D-187).
        "token",
        #: The client knows its own account: without it it cannot compute the
        #: device fee, and it is the one computing it (D-112). Unrelated to the hidden number.
        "account",
    }
    for answer in answers:
        assert set(answer) <= allowed_, f"лишние поля в ответе: {set(answer) - allowed_}"
    assert "roof" not in json.dumps(answers, ensure_ascii=False)


def test_face_not_opened_without_device_fee(client, miner, cheap_pow) -> None:
    """The Argon2id estimate is a precondition of the session, not decoration (D-110)."""
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json(_input(miner))
        ws.receive_json()

        ws.send_json({"cmd": "pow.challenge"})
        task = ws.receive_json()
        ws.send_json(
            {
                "cmd": "mine.start",
                "vein": miner["vein"],
                "challenge": task["challenge"],
                "answer": "00" * 32,
            }
        )
        assert "refused" in ws.receive_json()


def test_exploration_names_run_price_before_leaving(
    client, miner, constants: Constants
) -> None:
    """The run's price is a property of the place (D-156), and the client learns it before leaving.

    Untrodden surroundings must give a find in minutes: exploration is a
    newcomer's first meaningful action, and six hours of waiting kill it.
    """
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json(_input(miner))
        ws.receive_json()

        ws.send_json({"cmd": "explore.goals"})
        forecast = ws.receive_json()["outlook"]
        run = constants[R.EXPLORE_ATTEMPT_MINUTES]
        assert forecast["explored"] == 0
        assert forecast["minutes"] == {"min": run.min, "max": run.max}
        assert forecast["chance"] == constants[R.EXPLORE_FIND_CHANCE]
        assert 0 < forecast["stamina"] < constants[R.EXPLORE_ATTEMPT_STAMINA]


def test_command_without_hello_rejected(client, miner) -> None:
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"cmd": "mine.swing"})
        assert ws.receive_json()["refused"] == "сначала hello"


def test_unknown_command_does_not_drop_session(client, miner) -> None:
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json(_input(miner))
        ws.receive_json()
        ws.send_json({"cmd": "выдумка"})
        assert "нет такой команды" in ws.receive_json()["refused"]
        #: The session is alive -- a refusal by the rules is not the same as a failure.
        ws.send_json({"cmd": "mine.swing"})
        assert "refused" in ws.receive_json()


def test_login_by_name_abolished(client, miner) -> None:
    """Identification is email and password (D-187): a name no longer lets anyone in."""
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"cmd": "hello", "name": miner["name"]})
        assert "не подходят" in ws.receive_json()["refused"]
        ws.send_json({"cmd": "hello", "email": miner["email"], "password": "не тот"})
        assert "не подходят" in ws.receive_json()["refused"]


def test_token_identifies_instead_of_password(client, miner) -> None:
    """The password is entered once: reconnection goes by token, logout revokes it."""
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json(_input(miner))
        token = ws.receive_json()["token"]
        assert token

    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"cmd": "hello", "token": token})
        assert ws.receive_json()["hello"] == miner["name"]
        ws.send_json({"cmd": "account.profile"})
        profile = ws.receive_json()["profile"]
        assert profile["email"] == miner["email"]
        assert profile["line"] == "human"
        ws.send_json({"cmd": "account.update", "surname": "Каменный", "age": 40, "about": "шахтёр"})
        assert ws.receive_json()["profile"]["surname"] == "Каменный"
        ws.send_json({"cmd": "account.logout"})
        assert ws.receive_json()["bye"] is True

    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"cmd": "hello", "token": token})
        assert "истекла" in ws.receive_json()["refused"]


def test_registration_with_four_fields_and_nymphs_unavailable(client, miner) -> None:
    """Registration in one command: email, password, line, character, door (D-187)."""
    stamp = uuid.uuid4().hex[:6]
    order = {
        "cmd": "join",
        "email": f"Novice-{stamp}@Example.com",
        "password": "vosem-znakov",
        "password_again": "vosem-znakov",
        "line": "human",
        "name": f"Новичок-{stamp}",
        "surname": "Первый",
        "age": 22,
        "about": "только что напечатан",
    }
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json(order | {"line": "nymph"})
        assert "в разработке" in ws.receive_json()["refused"]
        ws.send_json(order | {"password_again": "другой"})
        assert "не совпадают" in ws.receive_json()["refused"]
        ws.send_json(order | {"password": "kor", "password_again": "kor"})
        assert "короче" in ws.receive_json()["refused"]
        ws.send_json(order)
        answer = ws.receive_json()
        assert answer["hello"] == order["name"] and answer["token"]
        ws.send_json({"cmd": "account.profile"})
        profile = ws.receive_json()["profile"]
        #: Email is stored lower-cased: one address -- one account.
        assert profile["email"] == order["email"].lower()
        assert profile["age"] == 22
        #: A taken email is not registered a second time.
        ws.send_json(order | {"name": f"Другой-{stamp}"})
        assert "занята" in ws.receive_json()["refused"]

    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"cmd": "hello", "email": order["email"], "password": order["password"]})
        assert ws.receive_json()["hello"] == order["name"]

    lines_ = client.get("/public/lines").json()["lines"]
    assert [credit_line["id"] for credit_line in lines_] == ["human", "nymph"]
    assert lines_[0]["playable"] and not lines_[1]["playable"]
    assert lines_[0]["players"] >= 2
