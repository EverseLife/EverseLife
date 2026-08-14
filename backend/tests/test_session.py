"""Сквозная проверка: полная сессия добычи через сессию клиента.

Главное здесь — последняя проверка. Скрытое число не должно утекать наружу
**ни в одном ответе**, а не только в том, о котором мы подумали.

Тест синхронный целиком: `TestClient` держит собственный цикл событий, и
готовить мир async-фикстурой рядом с ним — верный способ получить два цикла
и невоспроизводимые падения. Мир готовится отдельным прогоном.
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


async def _подготовить_мир() -> dict:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        node = await world.create_node(db, "terra.mine", "Забой", area_m2=100)
        vein = await world.create_vein(db, node, "Железная руда", richness=60, remaining=100_000)
        identity = await world.create_identity(db, f"Тэрн-{uuid.uuid4().hex[:6]}")
        body = await world.print_body(db, identity, node)
        сумка = await world.body_container(db, body)
        await world.grant_item(db, сумка, "Шахтная крепь", amount=5, origin="сценарий теста")
        account = await db.get(Account, identity.account_id)
        готово = {"name": identity.name, "vein": str(vein.id), "account": account.id}
        await db.commit()

    await engine.dispose()
    return готово


@pytest.fixture
def шахтёр(loaded) -> dict:
    try:
        return asyncio.run(_подготовить_мир())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"нет тестовой базы ({TEST_DATABASE_URL}): {exc}")


@pytest.fixture
def дёшево(constants: Constants) -> Constants:
    """Плата устройства по карману набору тестов — через горячую подмену."""
    cheap = constants.with_overrides({"pow.memory_per_session": 8, "pow.argon_iterations": 1})
    HOLDER.set(cheap)
    try:
        yield cheap
    finally:
        HOLDER.set(constants)


@pytest.fixture
def client(шахтёр, monkeypatch):
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


def _забой(ws, шахтёр: dict, cheap: Constants) -> dict:
    ws.send_json({"cmd": "pow.challenge"})
    задача = ws.receive_json()
    ответ = device.solve(cheap, шахтёр["account"], bytes.fromhex(задача["nonce"]))
    ws.send_json(
        {
            "cmd": "mine.start",
            "vein": шахтёр["vein"],
            "challenge": задача["challenge"],
            "answer": ответ.hex(),
        }
    )
    return ws.receive_json()


def test_полная_сессия_добычи(client, шахтёр, дёшево, constants: Constants) -> None:
    ответы = []
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"cmd": "hello", "name": шахтёр["name"]})
        привет = ws.receive_json()
        ответы.append(привет)
        assert привет["hello"] == шахтёр["name"]
        assert привет["body"]

        ответы.append(_забой(ws, шахтёр, дёшево))
        assert ответы[-1]["sign"], "признак идёт строкой, а не числом"
        assert ответы[-1]["mined"] == 0

        for _ in range(4):
            ws.send_json({"cmd": "mine.swing"})
            ответы.append(ws.receive_json())

        assert ответы[-1]["mined"] > 0
        assert ответы[-1]["swings"] == 4
        assert ответы[-1]["stamina"] < constants[R.BODY_STAMINA_MAX]

        ws.send_json({"cmd": "mine.timber"})
        ответы.append(ws.receive_json())
        assert ответы[-1]["timbers"] == 1

        ws.send_json({"cmd": "mine.pace", "pace": "fast"})
        ответы.append(ws.receive_json())
        assert ответы[-1]["pace"] == "fast"

        ws.send_json({"cmd": "mine.leave"})
        уход = ws.receive_json()
        ответы.append(уход)
        assert уход["haul"] > 0

    #: Наружу уходят только поля `Sight` плюс имя сессии. Ничего, из чего
    #: выводится устойчивость свода, в ответах нет — и это проверяется по
    #: всему, что ушло клиенту, а не по одному полю.
    разрешено = {
        "sign", "mined", "swings", "timbers", "stamina", "pace", "state", "session",
        "hello", "body", "node", "constants", "challenge", "nonce", "left", "haul",
        #: Свой аккаунт клиент знает: без него он не посчитает плату устройства,
        #: а считает её именно он (D-112). К скрытому числу это отношения не имеет.
        "account",
    }
    for ответ in ответы:
        assert set(ответ) <= разрешено, f"лишние поля в ответе: {set(ответ) - разрешено}"
    assert "roof" not in json.dumps(ответы, ensure_ascii=False)


def test_без_платы_устройства_забой_не_открыть(client, шахтёр, дёшево) -> None:
    """Оценка Argon2id — предусловие сессии, а не украшение (D-110)."""
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"cmd": "hello", "name": шахтёр["name"]})
        ws.receive_json()

        ws.send_json({"cmd": "pow.challenge"})
        задача = ws.receive_json()
        ws.send_json(
            {
                "cmd": "mine.start",
                "vein": шахтёр["vein"],
                "challenge": задача["challenge"],
                "answer": "00" * 32,
            }
        )
        assert "refused" in ws.receive_json()


def test_разведка_называет_цену_захода_до_выхода(
    client, шахтёр, constants: Constants
) -> None:
    """Цена захода — свойство места (D-156), и клиент узнаёт её до выхода.

    Нехоженая окрестность обязана отдавать находку за минуты: разведка — первое
    осмысленное действие новичка, и шесть часов ожидания её убивают.
    """
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"cmd": "hello", "name": шахтёр["name"]})
        ws.receive_json()

        ws.send_json({"cmd": "explore.goals"})
        прогноз = ws.receive_json()["outlook"]
        заход = constants[R.EXPLORE_ATTEMPT_MINUTES]
        assert прогноз["explored"] == 0
        assert прогноз["minutes"] == {"min": заход.min, "max": заход.max}
        assert прогноз["chance"] == constants[R.EXPLORE_FIND_CHANCE]
        assert 0 < прогноз["stamina"] < constants[R.EXPLORE_ATTEMPT_STAMINA]


def test_команда_без_знакомства_отклоняется(client, шахтёр) -> None:
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"cmd": "mine.swing"})
        assert ws.receive_json()["refused"] == "сначала hello"


def test_неизвестная_команда_не_роняет_сессию(client, шахтёр) -> None:
    with client.websocket_connect("/session/ws") as ws:
        ws.send_json({"cmd": "hello", "name": шахтёр["name"]})
        ws.receive_json()
        ws.send_json({"cmd": "выдумка"})
        assert "нет такой команды" in ws.receive_json()["refused"]
        #: Сессия жива — отказ по правилам не то же самое, что сбой.
        ws.send_json({"cmd": "mine.swing"})
        assert "refused" in ws.receive_json()
