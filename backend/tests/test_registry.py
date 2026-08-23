# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The command registry (wave 3): names are unique, both handler shapes run,
and every command the client sends exists on the server."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import src.api.session  # noqa: F401 -- registers the commands
from src.api.registry import COMMANDS, Command, Ctx, command

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


async def test_both_handler_shapes_are_served() -> None:
    async def old(state: dict, db: object, message: dict) -> dict:
        return {"shape": "state", "id": state["identity_id"]}

    async def new(ctx: Ctx) -> dict:
        return {"shape": "ctx", "id": ctx.identity_id}

    legacy = Command(name="t", handler=old, readonly=False, doc="", takes_ctx=False)
    modern = Command(name="t", handler=new, readonly=True, doc="", takes_ctx=True)
    state = {"identity_id": "who"}
    assert await legacy.run(state, None, {}) == {"shape": "state", "id": "who"}  # type: ignore[arg-type]
    assert await modern.run(state, None, {}) == {"shape": "ctx", "id": "who"}  # type: ignore[arg-type]


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
