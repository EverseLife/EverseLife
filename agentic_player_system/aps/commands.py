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


#: Arguments given in coins, not in the ten-thousandths a price is quoted in.
#: `finance.transfer` converts in the handler and the parser below finds it;
#: these three hand the bare number to `engine/bank.py`, which converts it
#: there -- across a call boundary the AST does not follow. Pinned by a test
#: against the game's source, so a rename there fails here.
COIN_ARGUMENTS = {
    "bank.borrow": ("amount",),
    "bank.repay": ("amount",),
    "city.bail": ("amount",),
}


def _is_uuid_call(node: ast.Call) -> bool:
    """`uuid.UUID(...)`, `_optional_uuid(...)` -- a parse into an identifier."""
    if isinstance(node.func, ast.Attribute):
        return node.func.attr.lower() == "uuid"
    return isinstance(node.func, ast.Name) and node.func.id.lower().endswith("uuid")


def _is_money_call(node: ast.Call) -> bool:
    """`money(...)` -- coins in, minor units out (`src/units.py`)."""
    return isinstance(node.func, ast.Name) and node.func.id == "money"


def _coin_keys(func: ast.AST) -> list[str]:
    """Arguments the handler itself converts from coins."""
    found: list[str] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and _is_money_call(node):
            for key in _keys(node):
                if key not in found:
                    found.append(key)
    return found


def _uuid_positions(func: ast.AsyncFunctionDef | ast.FunctionDef) -> list[int]:
    """Which of the function's own parameters it parses as an identifier.

    `_own_item(db, body, item_id)` does `uuid.UUID(item_id)` on its third
    parameter, so every handler calling `_own_item(db, body, message["item"])`
    wants an id in `item` -- the helper never touches the request itself, and
    without this the mark lands on `storage.take` and not on `storage.put`.
    """
    names = [argument.arg for argument in func.args.args]
    spots: list[int] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call) or not _is_uuid_call(node):
            continue
        for argument in node.args:
            if isinstance(argument, ast.Name) and argument.id in names:
                index = names.index(argument.id)
                if index not in spots:
                    spots.append(index)
    return spots


def _key_params(func: ast.AsyncFunctionDef | ast.FunctionDef) -> dict[str, dict[str, Any]]:
    """Parameters the function uses as a request key: `message[field]` where
    `field` is its own parameter with a string default.

    The game grew this shape with the farm rework: `_plot(db, message,
    field="plot")` reads whichever key the call site names and `plot` when it
    names none -- so the key is no longer a literal in anybody's body, and the
    reference went quietly thin (`farm.sow` lost `plot`). Recorded per
    parameter: its default, its position, and whether the read is parsed as an
    identifier (`uuid.UUID(message[field])`).
    """
    names = [argument.arg for argument in func.args.args]
    defaults: dict[str, str] = {}
    for argument, default in zip(names[len(names) - len(func.args.defaults) :], func.args.defaults):
        if isinstance(default, ast.Constant) and isinstance(default.value, str):
            defaults[argument] = default.value

    def read_by(node: ast.AST) -> str | None:
        """The parameter name when `node` is `message[param]`/`message.get(param)`."""
        if (
            isinstance(node, ast.Subscript)
            and _reads_message(node.value)
            and isinstance(node.slice, ast.Name)
        ):
            return node.slice.id
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _reads_message(node.func.value)
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            return node.args[0].id
        return None

    found: dict[str, dict[str, Any]] = {}
    for node in ast.walk(func):
        parses_id = isinstance(node, ast.Call) and _is_uuid_call(node)
        reads = node.args if parses_id else [node]
        for read in reads:
            param = read_by(read)
            if param is None or param not in defaults:
                continue
            entry = found.setdefault(
                param,
                {"default": defaults[param], "index": names.index(param), "id": False},
            )
            entry["id"] = entry["id"] or parses_id
    return found


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return call.func.attr if isinstance(call.func, ast.Attribute) else None


def _id_keys(func: ast.AST) -> list[str]:
    """Arguments the handler parses as an identifier rather than a name.

    The model passes what it can read -- the name of the thing where the
    handler wants the id of that particular one -- and the answer is
    `badly formed hexadecimal UUID string`, which says nothing about which
    argument was wrong. Marked in the reference, the question does not arise.
    """
    found: list[str] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and _is_uuid_call(node):
            for key in _keys(node):
                if key not in found:
                    found.append(key)
    return found


def argument_list(entry: dict[str, Any], separator: str = ",") -> str:
    """The arguments of one command: identifiers and sums in coins marked."""
    ids = set(entry.get("ids") or ())
    coins = set(entry.get("coins") or ())

    def marked(key: str) -> str:
        if key in ids:
            return f"{key}:id"
        return f"{key}:coins" if key in coins else key

    return separator.join(marked(key) for key in entry.get("keys") or ())


def _declared(node: ast.AsyncFunctionDef | ast.FunctionDef) -> tuple[str, bool] | None:
    """The name under `@command("...")` and whether it is declared `readonly`,
    when the function has one and it is not declared `hidden`.

    `readonly` is the game's own word about a command (`api/registry.py`), so
    the agent takes it from there instead of keeping a second list that drifts.

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
            return str(call.args[0].value), _flagged(call, "readonly")
    return None


def _flagged(call: ast.Call, name: str) -> bool:
    """Whether the decorator carries `name=True`."""
    return any(
        word.arg == name and isinstance(word.value, ast.Constant) and word.value.value is True
        for word in call.keywords
    )


def _helpers(source: str) -> dict[str, dict[str, list[str]]]:
    """Module-level functions that read request keys: the keys they read and
    which of them they parse as identifiers.

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
        spots = _uuid_positions(node)
        key_params = _key_params(node)
        if keys or spots or key_params:
            found[node.name] = {
                "keys": keys,
                "ids": _id_keys(node),
                "uuid_at": spots,
                "key_params": key_params,
            }
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


def extract(
    source: str, helpers: dict[str, dict[str, list[str]]] | None = None
) -> dict[str, dict[str, Any]]:
    """Commands of one module: every handler under `@command("name")` (the
    game's `api/registry.py`), its docstring and the request keys it reads --
    its own and those of the helpers it hands the request to."""
    tree = ast.parse(source)
    known = {**(helpers or {}), **_helpers(source)}
    reference: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        declaration = _declared(node)
        if declaration is None:
            continue
        command, readonly = declaration
        keys = _keys(node)
        ids = _id_keys(node)
        #: What a helper reads, the command asks for -- and what the helper
        #: parses as an identifier, the command wants as one (`craft.start`
        #: takes `tool` through `_craft_request`).
        for helper in _passed_on(node):
            borrowed = known.get(helper) or {}
            for key in borrowed.get("keys", ()):
                if key not in keys:
                    keys.append(key)
            for key in borrowed.get("ids", ()):
                if key not in ids:
                    ids.append(key)
        #: And the commonest shape of all: not the whole request, one value out
        #: of it -- `_own_item(db, body, message["item"])`. The helper says
        #: which of its parameters it parses as an identifier; the call site
        #: says which key sits there.
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            helper = known.get(_called_name(call) or "") or {}
            for index in helper.get("uuid_at") or ():
                if index < len(call.args):
                    for key in _keys(call.args[index]):
                        if key not in ids:
                            ids.append(key)
            #: A helper whose key is its own parameter (`_plot(db, message,
            #: field="plot")`): the call site names the key, or the default is
            #: the key. Only when the request itself is handed over -- a call
            #: that never passes `message` reads nothing for this command.
            passes_message = any(
                _reads_message(argument)
                for argument in list(call.args) + [word.value for word in call.keywords]
            )
            for param, spec in (helper.get("key_params") or {}).items() if passes_message else ():
                key = spec["default"]
                index = spec["index"]
                if index < len(call.args) and isinstance(call.args[index], ast.Constant):
                    key = call.args[index].value
                for word in call.keywords:
                    if word.arg == param and isinstance(word.value, ast.Constant):
                        key = word.value.value
                if not isinstance(key, str):
                    continue
                if key not in keys:
                    keys.append(key)
                if spec["id"] and key not in ids:
                    ids.append(key)
        reference[command] = {
            "doc": ast.get_docstring(node) or "",
            "keys": keys,
            "readonly": readonly,
            "ids": ids,
            "coins": _coin_keys(node)
            + [key for key in COIN_ARGUMENTS.get(command, ()) if key in keys],
        }
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
                for name, read in _helpers(module.read_text(encoding="utf-8")).items():
                    helpers.setdefault(name, read)
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
        lines.append(f"- {command}({argument_list(entry)}): {headline(entry['doc'])}")
    return "\n".join(lines)


def help_text(reference: dict[str, dict[str, Any]], command: str) -> str:
    entry = reference.get(command)
    if entry is None:
        return f"Нет такой команды: {command}"
    args = argument_list(entry, ", ") or "без аргументов"
    hint = "\n«:id» — идентификатор из ответа сервера, а не название." if entry.get("ids") else ""
    if entry.get("coins"):
        hint += "\n«:coins» — сумма в монетах, а не в мелких долях, как цена."
    return f"{command}\nАргументы: {args}{hint}\n\n{entry['doc'] or '(описания нет)'}"
