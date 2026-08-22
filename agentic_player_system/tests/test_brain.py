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

SESSION_SOURCE = Path(__file__).resolve().parents[2] / "backend" / "src" / "api" / "session.py"


class FakeGame:
    def __init__(self, script: dict[str, Any]) -> None:
        self.script = script
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def act(self, cmd: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sent.append((cmd, dict(args or {})))
        answer = self.script.get(cmd, {"ok": True})
        if isinstance(answer, Refused):
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
                    _call("remember", notes="план: сначала четыре здания"),
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
    assert kinds == ["look", "refused", "bug", "thought"]
    assert store.agent(agent["id"])["notes"] == "план: сначала четыре здания"
    assert store.reports()[0]["text"] == "город не основался"
    assert store.usage_today(agent["id"])["total"] == 440
    assert game.sent[0] == ("look", {}) and game.sent[1] == ("city.found", {"name": "Новгород"})


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


def test_shrink_caps_lists_and_strings() -> None:
    packed = brain.shrink({"items": list(range(100)), "text": "x" * 1000})
    assert len(packed["items"]) == brain.MAX_LIST + 1
    assert packed["text"].endswith("…")
