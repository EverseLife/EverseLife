# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The agent tests' shared ground: a scripted game, a scripted tool call, and
where the session's own command registry is read from.

Used by the `test_brain.py` family -- the turn loop, the command reference and
the advice a refusal carries with it; not collected by pytest. No real fixture
lives here on purpose -- a `@pytest.fixture` in a kit is imported for its name
alone, ruff removes the import as unused, and pytest then never finds the
fixture. The fixtures are next door, in `conftest.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
        #: `public:renames` in the script feeds the names table (D-251).
        return self.script.get(f"public:{path}", {"path": path})


def _arguments(hint: str, cmd: str) -> set[str]:
    """Which arguments a refusal's hint names for this command.

    Read out of the line rather than compared to it whole: the list comes from
    the server's own command registry, so a new argument on any command would
    otherwise fail a test about the hint's shape rather than about its content.
    """
    for line in hint.splitlines():
        if line.startswith(f"Аргументы {cmd}: "):
            said = line.removeprefix(f"Аргументы {cmd}: ").split(" — ")[0]
            return {one.strip() for one in said.split(",") if one.strip()}
    return set()


def _call(name: str, **arguments: Any) -> dict[str, Any]:
    return {
        "id": f"call-{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
