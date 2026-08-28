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
import re
from pathlib import Path
from typing import Any

#: Commands that live outside the handler table; the client does them itself.
BUILTIN = {
    "hello": {"doc": "Identification by token or email+password. Done for you.", "keys": []},
    "join": {"doc": "Registration of a new account. Done for you.", "keys": []},
}


def _reads_message(node: ast.AST) -> bool:
    """`message` itself, or the `ctx.message` of a handler taking a context."""
    if isinstance(node, ast.Name):
        return node.id == "message"
    return isinstance(node, ast.Attribute) and node.attr == "message"


def _keys(func: ast.AST) -> list[str]:
    """Argument names the handler reads out of the request.

    Three shapes, because the game is migrating handler by handler from
    `(state, db, message)` to a context object (`api/registry.Ctx`, review
    2026-08-23): `message.get("x")` / `message["x"]`, the same through
    `ctx.message`, and `ctx.arg("x")`.
    """
    found: list[str] = []
    for node in ast.walk(func):
        key = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
            first = node.args[0]
            reader = node.func
            if isinstance(first, ast.Constant) and (
                (reader.attr == "get" and _reads_message(reader.value))
                #: `ctx.arg("node")` -- the context's own reader.
                or (
                    reader.attr == "arg"
                    and isinstance(reader.value, ast.Name)
                    and reader.value.id == "ctx"
                )
            ):
                key = first.value
        elif (
            isinstance(node, ast.Subscript)
            and _reads_message(node.value)
            and isinstance(node.slice, ast.Constant)
        ):
            key = node.slice.value
        if isinstance(key, str) and key not in found:
            found.append(key)
    return found


def _command_name(node: ast.AsyncFunctionDef | ast.FunctionDef) -> str | None:
    """The name under `@command("...")`, when the function has one and it is
    not declared `hidden`.

    A hidden command is one only a developer may run -- the alpha's debug
    widget (D-229). Its handler refuses everyone else on its own; leaving it
    out of the reference is about not putting the idea into every agent's
    prompt, where it would read as one more thing to try and one more refusal
    to reason about.
    """
    for decorator in node.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        if (
            call is not None
            and isinstance(call.func, ast.Name)
            and call.func.id == "command"
            and call.args
            and isinstance(call.args[0], ast.Constant)
        ):
            if _flagged(call, "hidden"):
                return None
            return str(call.args[0].value)
    return None


def _flagged(call: ast.Call, name: str) -> bool:
    """Whether the decorator carries `name=True`."""
    return any(
        word.arg == name and isinstance(word.value, ast.Constant) and word.value.value is True
        for word in call.keywords
    )


def _helpers(source: str) -> dict[str, list[str]]:
    """Module-level functions that read request keys, and the keys they read.

    Only those: a function that never touches the request tells the reference
    nothing, and leaving it out keeps the map small enough to search the
    engine's modules too, where a couple of parsers live (`check_profile`).
    """
    tree = ast.parse(source)
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        keys = _keys(node)
        if keys:
            found[node.name] = keys
    return found


def _passed_on(func: ast.AST) -> list[str]:
    """Names of functions this handler hands the whole request to.

    `craft.plan` parses its request with `_craft_request(message)` so that the
    forecast and the batch read it the same way -- and the handler's own body
    then names no key at all. Believing that, the model called the command
    bare and got `KeyError('output')` (agents' finding, 2026-08-23). What a
    helper reads, the command asks for.
    """
    called: list[str] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name is None or name in called:
            continue
        arguments = list(node.args) + [word.value for word in node.keywords]
        if any(_reads_message(argument) for argument in arguments):
            called.append(name)
    return called


def extract(source: str, helpers: dict[str, list[str]] | None = None) -> dict[str, dict[str, Any]]:
    """Commands of one module: every handler under `@command("name")` (the
    game's `api/registry.py`), its docstring and the request keys it reads --
    its own and those of the helpers it hands the request to."""
    tree = ast.parse(source)
    known = {**(helpers or {}), **_helpers(source)}
    reference: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        command = _command_name(node)
        if command is None:
            continue
        keys = _keys(node)
        for helper in _passed_on(node):
            for key in known.get(helper, ()):
                if key not in keys:
                    keys.append(key)
        reference[command] = {"doc": ast.get_docstring(node) or "", "keys": keys}
    return reference


def load(path: Path, cached: str = "") -> dict[str, dict[str, Any]]:
    """Read the reference from the source -- the `api/commands/` package, one
    module per domain -- and fall back to a cached JSON copy."""
    if path.is_dir():
        modules = {
            module: module.read_text(encoding="utf-8") for module in sorted(path.glob("*.py"))
        }
        #: Helpers of the whole package first: a handler may parse its request
        #: with one borrowed from a neighbouring module (`common.py`) or from
        #: the engine itself (`account.check_profile`). The package wins on a
        #: name it shares with the engine.
        helpers: dict[str, list[str]] = {}
        engine = path.parent.parent / "engine"
        for source in modules.values():
            for name, keys in _helpers(source).items():
                helpers.setdefault(name, keys)
        for module in sorted(engine.glob("*.py")) if engine.is_dir() else ():
            try:
                for name, keys in _helpers(module.read_text(encoding="utf-8")).items():
                    helpers.setdefault(name, keys)
            except (OSError, SyntaxError):  # pragma: no cover -- a half-written file
                continue
        reference = dict(BUILTIN)
        for source in modules.values():
            reference.update(extract(source, helpers))
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


#: The reference rides in every prompt of every turn, so a command gets one
#: clause here and the rest through `help`: a whole first docstring line is
#: mostly sentences about the mechanics, which do not help pick a command, and
#: the vault numbers in them (`D-172`) mean nothing to the model at all. Half
#: the reference is bought back this way -- on a local model the prompt is the
#: whole budget.
HEADLINE_LIMIT = 72
#: `(D-172)`, `(04-notifications)`: a pointer into the vault, not into the game.
_VAULT_REF = re.compile(r"\s*\([^()]*(?:D-\d+|\d\d-[a-z])[^()]*\)")
#: A full stop or a semicolon ends the clause; a colon does not -- in these
#: docstrings it introduces the substance ("Buy: a limit order from a present
#: body"), and the half after it is the half that tells commands apart.
_CLAUSE_END = re.compile(r"(?<=[.;])\s")


def headline(doc: str, limit: int = HEADLINE_LIMIT) -> str:
    """The first clause of a docstring: what the command does, and no more."""
    line = _VAULT_REF.sub("", doc.strip().split("\n", 1)[0]).strip()
    clause = _CLAUSE_END.split(line, maxsplit=1)[0].rstrip(" .;:")
    if len(clause) > limit:
        clause = clause[:limit].rsplit(" ", 1)[0] + "…"
    return clause


def brief(reference: dict[str, dict[str, Any]]) -> str:
    """One line per command: the name, the arguments, one clause of the doc."""
    lines = []
    for command, entry in sorted(reference.items()):
        if command in BUILTIN:
            continue
        args = ",".join(entry["keys"])
        lines.append(f"- {command}({args}): {headline(entry['doc'])}")
    return "\n".join(lines)


def help_text(reference: dict[str, dict[str, Any]], command: str) -> str:
    entry = reference.get(command)
    if entry is None:
        return f"Нет такой команды: {command}"
    args = ", ".join(entry["keys"]) or "без аргументов"
    return f"{command}\nАргументы: {args}\n\n{entry['doc'] or '(описания нет)'}"
