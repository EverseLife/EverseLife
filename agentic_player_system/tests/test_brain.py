# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The brain with a scripted model and a scripted game: no network, no provider."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from aps import brain, commands, llm
from aps.game import Refused
from aps.runner import Runner
from aps.store import Store

SESSION_SOURCE = Path(__file__).resolve().parents[2] / "backend" / "src" / "api" / "commands"


class FakeGame:
    def __init__(self, script: dict[str, Any]) -> None:
        self.script = script
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.reconnects = 0

    async def reconnect(self) -> None:
        self.reconnects += 1

    #: The two-way socket (D-226): the fake has heard nothing unless told.
    events: list[dict[str, Any]] = []  # noqa: RUF012 -- a test double, reset per test

    async def drain(self) -> None:
        pass

    def take_events(self) -> list[dict[str, Any]]:
        taken, self.events = self.events, []
        return taken

    async def act(self, cmd: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sent.append((cmd, dict(args or {})))
        answer = self.script.get(cmd, {"ok": True})
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def public(self, path: str) -> Any:
        return {"path": path}


def _call(name: str, **arguments: Any) -> dict[str, Any]:
    return {
        "id": f"call-{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "aps.sqlite3")


@pytest.fixture
def agent(store: Store) -> dict[str, Any]:
    return store.create_agent(
        {"name": "Тестер", "email": "t@example.com", "password": "secret", "goal": "основать город"}
    )


async def test_turn_records_actions_refusals_notes_and_thought(
    monkeypatch: pytest.MonkeyPatch, store: Store, agent: dict[str, Any]
) -> None:
    replies = iter(
        [
            llm.Reply(
                content="",
                tool_calls=[_call("act", cmd="city.found", args={"name": "Новгород"})],
                prompt_tokens=100,
                completion_tokens=10,
            ),
            llm.Reply(
                content="",
                tool_calls=[_call("help", cmd="city.found")],
                prompt_tokens=100,
                completion_tokens=10,
            ),
            llm.Reply(
                content="",
                tool_calls=[_call("report_bug", text="город не основался")],
                prompt_tokens=100,
                completion_tokens=10,
            ),
            llm.Reply(
                content="",
                tool_calls=[
                    _call("note_add", text="план: сначала четыре здания"),
                    _call("finish", thought="надо строить"),
                ],
                prompt_tokens=100,
                completion_tokens=10,
            ),
        ]
    )

    async def fake_chat(*_: Any, **__: Any) -> llm.Reply:
        return next(replies)

    monkeypatch.setattr(llm, "chat", fake_chat)
    game = FakeGame({"look": {"money": 0}, "city.found": Refused("нужно четыре здания")})
    reference = commands.load(SESSION_SOURCE)

    turn = await brain.run_turn(
        agent=agent,
        game=game,
        store=store,
        provider=llm.Provider("u", "k", "m"),
        reference=reference,  # type: ignore[arg-type]
    )

    assert turn.finished and turn.thought == "надо строить"
    assert turn.steps == 5
    assert turn.prompt_tokens == 400
    kinds = [e["kind"] for e in store.events(agent["id"])]
    assert kinds == [
        "look",
        "prompt",
        "model",
        "refused",
        "model",
        "model",
        "bug",
        "model",
        "thought",
    ]
    assert store.events(agent["id"])[0]["text"] == "full"
    assert store.agent(agent["id"])["notes"] == "план: сначала четыре здания"
    model_events = [e for e in store.events(agent["id"]) if e["kind"] == "model"]
    assert json.loads(model_events[0]["reply"])["tool_calls"][0]["name"] == "act"
    assert store.reports()[0]["text"] == "город не основался"
    assert store.usage_today(agent["id"])["total"] == 440
    assert game.sent[0] == ("look", {}) and game.sent[1] == ("city.found", {"name": "Новгород"})
    assert game.sent[-1] == ("look", {}) and turn.busy_until is None


async def test_plain_text_reply_ends_the_turn(
    monkeypatch: pytest.MonkeyPatch, store: Store, agent: dict[str, Any]
) -> None:
    async def fake_chat(*_: Any, **__: Any) -> llm.Reply:
        return llm.Reply(content="Подожду следующего хода.")

    monkeypatch.setattr(llm, "chat", fake_chat)
    turn = await brain.run_turn(
        agent=agent,
        game=FakeGame({}),
        store=store,
        provider=llm.Provider("u", "k", "m"),
        reference={},  # type: ignore[arg-type]
    )
    assert turn.finished and turn.steps == 0
    assert store.events(agent["id"])[-1]["text"] == "Подожду следующего хода."
    #: Memory is the agent's own business: nothing is written for it.
    assert store.agent(agent["id"])["notes"] == ""


async def test_notes_are_edited_entry_by_entry_and_never_truncated(
    store: Store, agent: dict[str, Any]
) -> None:
    common = {
        "agent": agent,
        "game": FakeGame({}),
        "store": store,
        "reference": {},
        "turn": brain.Turn(),
    }

    async def call(name: str, **arguments: Any) -> str:
        return await brain._tool(name, arguments, **common)  # type: ignore[arg-type]

    await call("note_add", text="раз")
    await call("note_add", text="два\nс переносом")
    await call("note_add", text="три")
    assert brain.render_notes(agent["notes"]) == "#1 раз\n#2 два с переносом\n#3 три"
    assert (await call("note_edit", id=2, text="два")).startswith("Запись #2 заменена")
    assert (await call("note_delete", id=1)).startswith("Запись #1 удалена")
    assert store.agent(agent["id"])["notes"] == "два\nтри"
    assert (await call("note_delete", id=9)).startswith("Нет записи #9")

    answer = await call("note_add", text="x" * brain.MAX_NOTES_CHARS)
    assert answer.startswith("Память заполнена")
    assert store.agent(agent["id"])["notes"] == "два\nтри"
    answer = await call("note_edit", id=1, text="y" * brain.MAX_NOTES_CHARS)
    assert "переполнится" in answer and store.agent(agent["id"])["notes"] == "два\nтри"


def test_busy_until_is_the_latest_running_occupation() -> None:
    from datetime import UTC, datetime, timedelta

    soon = datetime.now(UTC) + timedelta(minutes=5)
    later = datetime.now(UTC) + timedelta(minutes=9)
    past = datetime.now(UTC) - timedelta(minutes=1)
    seen = {
        "look": {
            "travel": {"arrives_at": soon.isoformat()},
            "doings": [
                {"kind": "survey", "until": later.isoformat()},
                {"kind": "x", "until": None},
            ],
        }
    }
    assert brain.busy_until(seen) == later
    assert brain.busy_until({"look": {"travel": None, "doings": []}}) is None
    assert brain.busy_until({"look": {"doings": [{"until": past.isoformat()}]}}) is None


async def test_finish_can_ask_to_wait(store: Store, agent: dict[str, Any]) -> None:
    turn = brain.Turn()
    common = {"agent": agent, "game": FakeGame({}), "store": store, "reference": {}, "turn": turn}
    await brain._tool("finish", {"thought": "жду партию", "wait_seconds": 1800}, **common)  # type: ignore[arg-type]
    assert turn.finished and turn.wait_seconds == 1800
    await brain._tool("finish", {"thought": "x", "wait_seconds": 10**9}, **common)  # type: ignore[arg-type]
    assert turn.wait_seconds == brain.MAX_WAIT


def test_journal_pages_both_ways_and_filters_by_kind(store: Store, agent: dict[str, Any]) -> None:
    for i in range(7):
        store.event(agent["id"], "model" if i % 2 else "action", text=str(i))
    newest = store.events(agent["id"], limit=3)
    assert [e["text"] for e in newest] == ["4", "5", "6"]
    older = store.events(agent["id"], limit=3, before=newest[0]["id"])
    assert [e["text"] for e in older] == ["1", "2", "3"]
    newer = store.events(agent["id"], limit=3, after=older[-1]["id"])
    assert [e["text"] for e in newer] == ["4", "5", "6"]
    only_model = store.events(agent["id"], limit=10, kinds=("model",))
    assert [e["text"] for e in only_model] == ["1", "3", "5"]


async def test_a_dropped_socket_is_reconnected_and_the_turn_goes_on(
    store: Store, agent: dict[str, Any]
) -> None:
    from aps.game import GameError

    turn = brain.Turn()
    game = FakeGame({"travel.go": GameError("соединение оборвалось: no close frame")})
    common = {"agent": agent, "game": game, "store": store, "reference": {}, "turn": turn}
    answer = await brain._tool("act", {"cmd": "travel.go", "args": {"to": "x"}}, **common)  # type: ignore[arg-type]
    assert "восстановлена" in answer and game.reconnects == 1
    assert not turn.finished
    assert store.events(agent["id"])[-1]["kind"] == "error"


def test_observation_is_a_digest_with_changes_and_the_whole_look_every_few_turns() -> None:
    from aps import observe

    first = {
        "look": {
            "identity": "Марта",
            "money": "120",
            "body": {"stamina": 90.0, "sleeping_since": None},
            "node": {"name": "Ядро", "key": "terra.capital.core", "stations": ["биопринтер"]},
            "inventory": [{"goods": "Хлеб", "amount": 2}],
            "doings": [],
            "travel": None,
            "clock": {"now": "1"},
        }
    }
    second = json.loads(json.dumps(first))
    second["look"]["money"] = "95"
    second["look"]["inventory"].append({"goods": "Кирка", "amount": 1})
    second["look"]["clock"]["now"] = "2"

    text, mode = observe.observation(None, first, full=False, packed="{}")
    assert mode == "full" and "Полный look" in text
    text, mode = observe.observation(first, second, full=False, packed="x" * 5000)
    assert mode == "delta"
    assert "деньги 95" in text and "Сумка (2)" in text
    assert "money: 120 → 95" in text and "появилось Кирка×1" in text
    assert "clock" not in text
    text, mode = observe.observation(first, second, full=True, packed="{}")
    assert mode == "full"
    #: A diff no shorter than the whole thing is pointless: show the whole thing.
    text, mode = observe.observation(first, second, full=False, packed="{}")
    assert mode == "full"


def test_stuck_detection_needs_the_same_refused_action_in_a_row() -> None:
    turn = brain.Turn(actions=[("travel.go", "{}", False)] * 4)
    assert Runner._stuck(turn)
    turn = brain.Turn(actions=[("travel.go", "{}", False)] * 3 + [("look", "{}", False)])
    assert not Runner._stuck(turn)
    turn = brain.Turn(actions=[("travel.go", "{}", True)] * 4)
    assert not Runner._stuck(turn)


def test_reference_is_extracted_from_the_session_source() -> None:
    reference = commands.load(SESSION_SOURCE)
    assert "city.found" in reference and "ship.found" in reference
    assert reference["ship.found"]["keys"] == ["name"]
    assert "spaceport" in reference["ship.found"]["doc"]
    assert "- city.found(name):" in commands.brief(reference)
    #: Every registered command is in the reference, and none without a doc:
    #: the model reads the reference, not the code.
    import subprocess
    import sys

    listed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from src.api.registry import COMMANDS; import src.api.session; print(len(COMMANDS))",
        ],
        cwd=SESSION_SOURCE.parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.returncode == 0:
        assert len(reference) - 2 == int(listed.stdout.strip()), (
            "справочник не совпадает с реестром"
        )


def test_shrink_caps_lists_and_strings() -> None:
    packed = brain.shrink({"items": list(range(100)), "text": "x" * 1000})
    assert len(packed["items"]) == brain.MAX_LIST + 1
    assert packed["text"].endswith("…")


def test_events_heard_between_turns_open_the_observation() -> None:
    from aps import observe

    told = observe.happened(
        [
            {"event": "knowledge.learned", "seq": 5, "touches": ["knowledge"], "key": "Кирка"},
            {
                "event": "travel.arrived",
                "seq": 6,
                "touches": ["body", "node"],
                "who": "Тэрн",
                "node": {"key": "terra.mine", "name": "Забой"},
            },
        ]
    )
    assert told.splitlines() == [
        "- knowledge.learned · key: Кирка",
        '- travel.arrived · кто: Тэрн · node: {"key": "terra.mine", "name": "Забой"}',
    ]
    assert observe.happened([]) == ""
