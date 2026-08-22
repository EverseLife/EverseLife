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


def extract(source: str) -> dict[str, dict[str, Any]]:
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    table: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_COMMANDS" for t in node.targets)
            and isinstance(node.value, ast.Dict)
        ):
            for key, value in zip(node.value.keys, node.value.values, strict=True):
                if isinstance(key, ast.Constant) and isinstance(value, ast.Name):
                    table[str(key.value)] = value.id
    reference: dict[str, dict[str, Any]] = dict(BUILTIN)
    for command, name in table.items():
        func = functions.get(name)
        if func is None:
            reference[command] = {"doc": "", "keys": []}
            continue
        reference[command] = {"doc": ast.get_docstring(func) or "", "keys": _keys(func)}
    return reference


def load(path: Path, cached: str = "") -> dict[str, dict[str, Any]]:
    """Read the reference from the source; fall back to a cached JSON copy."""
    if path.exists():
        return extract(path.read_text(encoding="utf-8"))
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
