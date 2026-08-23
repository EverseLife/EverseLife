# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The client protocol reference, read from the game's `session.py` by `ast`.

The agent needs to know the commands the way a player knows the buttons. The
server's handlers already document each one in a docstring and read their
arguments with `message.get("...")` / `message["..."]`, so the reference is
extracted rather than written twice. No import of the game code happens: the
source file is parsed as text.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

#: Commands that live outside the handler table; the client does them itself.
BUILTIN = {
    "hello": {"doc": "Identification by token or email+password. Done for you.", "keys": []},
    "join": {"doc": "Registration of a new account. Done for you.", "keys": []},
}


def _keys(func: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(func):
        key = None
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "message"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            key = node.args[0].value
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "message"
            and isinstance(node.slice, ast.Constant)
        ):
            key = node.slice.value
        if isinstance(key, str) and key not in found:
            found.append(key)
    return found


def _command_name(node: ast.AsyncFunctionDef | ast.FunctionDef) -> str | None:
    """The name under `@command("...")`, when the function has one."""
    for decorator in node.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        if (
            call is not None
            and isinstance(call.func, ast.Name)
            and call.func.id == "command"
            and call.args
            and isinstance(call.args[0], ast.Constant)
        ):
            return str(call.args[0].value)
    return None


def extract(source: str) -> dict[str, dict[str, Any]]:
    """Commands of one module: every handler under `@command("name")` (the
    game's `api/registry.py`), its docstring and the message keys it reads."""
    tree = ast.parse(source)
    reference: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        command = _command_name(node)
        if command is not None:
            reference[command] = {"doc": ast.get_docstring(node) or "", "keys": _keys(node)}
    return reference


def load(path: Path, cached: str = "") -> dict[str, dict[str, Any]]:
    """Read the reference from the source -- the `api/commands/` package, one
    module per domain -- and fall back to a cached JSON copy."""
    if path.is_dir():
        reference = dict(BUILTIN)
        for module in sorted(path.glob("*.py")):
            reference.update(extract(module.read_text(encoding="utf-8")))
        return reference
    if path.exists():
        found = extract(path.read_text(encoding="utf-8"))
        if not found:
            #: An old `.env` still points at `session.py`: the commands moved to
            #: `api/commands/` and an empty reference is a silent agent.
            raise RuntimeError(
                f"{path}: no @command handlers; point APS_SESSION_SOURCE at backend/src/api/commands"
            )
        return dict(BUILTIN) | found
    if cached:
        return json.loads(cached)
    return dict(BUILTIN)


def brief(reference: dict[str, dict[str, Any]]) -> str:
    """One line per command: the name, the arguments, the first line of the doc."""
    lines = []
    for command, entry in sorted(reference.items()):
        if command in BUILTIN:
            continue
        first = entry["doc"].strip().split("\n", 1)[0].strip()
        args = ", ".join(entry["keys"])
        lines.append(f"- {command}({args}): {first}")
    return "\n".join(lines)


def help_text(reference: dict[str, dict[str, Any]], command: str) -> str:
    entry = reference.get(command)
    if entry is None:
        return f"Нет такой команды: {command}"
    args = ", ".join(entry["keys"]) or "без аргументов"
    return f"{command}\nАргументы: {args}\n\n{entry['doc'] or '(описания нет)'}"
