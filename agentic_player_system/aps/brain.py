# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""One turn of an agent: look, think, act a few times, write a note for later.

The model sees what the player sees (`look`), the protocol reference, its own
notes and its last actions. It acts with tools; each tool call becomes an event
in the journal, each refusal is recorded verbatim -- that is the finding.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from . import commands, llm
from .game import Game, GameError, Refused
from .store import Store

log = logging.getLogger(__name__)

MAX_LIST = 25
MAX_STRING = 400
MAX_REPLY_CHARS = 9000
MAX_NOTES_CHARS = 4000
HISTORY = 20

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "act",
            "description": "Send a game command over the session (the only way to act).",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Command name, e.g. travel.go"},
                    "args": {
                        "type": "object",
                        "description": "Command arguments as a JSON object",
                        "additionalProperties": True,
                    },
                },
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "help",
            "description": "Full description of one command: arguments and what it does.",
            "parameters": {
                "type": "object",
                "properties": {"cmd": {"type": "string"}},
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": (
                "Public read, no identity needed: doors, map, lines, recipes, plants, laws, "
                "market/{node_key}, market/{node_key}/book, quality/tiers."
            ),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Replace your notes -- the only memory that survives to the next turn. "
                "Keep the plan, what worked, what was refused and why, ids you need."
            ),
            "parameters": {
                "type": "object",
                "properties": {"notes": {"type": "string"}},
                "required": ["notes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_bug",
            "description": (
                "Tell the developers that something looks broken: a refusal that contradicts "
                "the rules, an impossible state, a command that does nothing. Not for 'I am poor'."
            ),
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "End this turn. Say in one or two sentences what you did and what is next.",
            "parameters": {
                "type": "object",
                "properties": {"thought": {"type": "string"}},
                "required": ["thought"],
            },
        },
    },
]

SYSTEM = """Ты — житель мира Everse.Life, обычный игрок. Тебя зовут {name}.
Ты действуешь в игре только через инструмент act: это те же команды, которыми пользуется
клиент игры. Мир честный и медленный: денег с неба нет, всё добывается, делается и
покупается; долгие работы идут по расписанию, результат приходит позже.

Твой характер: {persona}

Твоя цель: {goal}

Как играть:
- Сначала посмотри, что ты видишь (look уже сделан за тебя — смотри «Наблюдение»).
- Если не уверен в аргументах команды — вызови help. Отказ сервера — нормальная часть игры:
  прочитай причину и действуй иначе. Не повторяй одно и то же действие, если оно отказано.
- Публичные каталоги (двери, карта, рынки, рецепты) — через read.
- Если отказ противоречит правилам или мир ведёт себя невозможным образом — report_bug.
- В конце хода обязательно обнови заметки (remember) и заверши ход (finish).
  Заметки — твоя единственная память. Пиши в них план, найденные id, что удалось и что нет.
- Ходов немного: за один ход не больше {max_steps} вызовов инструментов.

Команды сессии (имя(аргументы): что делает):
{reference}
"""


def shrink(value: Any, *, depth: int = 0) -> Any:
    if isinstance(value, dict):
        return {k: shrink(v, depth=depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        cut = [shrink(v, depth=depth + 1) for v in value[:MAX_LIST]]
        if len(value) > MAX_LIST:
            cut.append(f"... ещё {len(value) - MAX_LIST}")
        return cut
    if isinstance(value, str) and len(value) > MAX_STRING:
        return value[:MAX_STRING] + "…"
    return value


def pack(value: Any, limit: int = MAX_REPLY_CHARS) -> str:
    text = json.dumps(shrink(value), ensure_ascii=False)
    if len(text) > limit:
        text = text[:limit] + "…(обрезано)"
    return text


@dataclass
class Turn:
    steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    actions: list[tuple[str, str, bool]] = field(default_factory=list)
    finished: bool = False
    thought: str = ""


def _history(store: Store, agent_id: str) -> str:
    lines = []
    for event in store.recent(agent_id, ("action", "refused", "thought", "bug"), HISTORY):
        if event["kind"] == "thought":
            lines.append(f"[{event['at']}] мысль: {event['text']}")
        elif event["kind"] == "bug":
            lines.append(f"[{event['at']}] заявил дефект: {event['text']}")
        else:
            mark = "ОТКАЗ" if event["kind"] == "refused" else "ок"
            detail = event["text"] if event["kind"] == "refused" else event["reply"][:200]
            lines.append(f"[{event['at']}] {event['cmd']} {event['request']} → {mark}: {detail}")
    return "\n".join(lines) or "(ещё ничего не делал)"


async def run_turn(
    *,
    agent: dict[str, Any],
    game: Game,
    store: Store,
    provider: llm.Provider,
    reference: dict[str, dict[str, Any]],
) -> Turn:
    turn = Turn()
    agent_id = agent["id"]
    max_steps = int(agent["max_steps"] or 8)

    try:
        seen = await game.act("look")
    except Refused as refusal:
        seen = {"refused": str(refusal)}
    store.event(agent_id, "look", cmd="look", reply=shrink(seen))

    system = SYSTEM.format(
        name=agent["name"],
        persona=agent["persona"] or "спокойный, практичный, любопытный",
        goal=agent["goal"] or "жить, зарабатывать и обустраиваться",
        max_steps=max_steps,
        reference=commands.brief(reference),
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Твои заметки:\n{agent['notes'] or '(пусто)'}\n\n"
                f"Последние действия:\n{_history(store, agent_id)}\n\n"
                f"Наблюдение (look):\n{pack(seen)}\n\n"
                "Твой ход."
            ),
        },
    ]

    while turn.steps < max_steps and not turn.finished:
        reply = await llm.chat(provider, messages, TOOLS, model=agent["model"] or "")
        turn.prompt_tokens += reply.prompt_tokens
        turn.completion_tokens += reply.completion_tokens
        store.add_usage(agent_id, reply.prompt_tokens, reply.completion_tokens)
        messages.append(reply.raw_message)

        if not reply.tool_calls:
            #: Plain text without a tool call: treat it as the closing thought.
            turn.thought = reply.content.strip()
            turn.finished = True
            break

        for call in reply.tool_calls:
            turn.steps += 1
            function = call.get("function") or {}
            name = function.get("name") or ""
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            result = await _tool(
                name, arguments, agent=agent, game=game, store=store, reference=reference, turn=turn
            )
            messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": result})
            if turn.finished:
                break

    if turn.thought:
        store.event(agent_id, "thought", text=turn.thought)
    return turn


async def _tool(
    name: str,
    arguments: dict[str, Any],
    *,
    agent: dict[str, Any],
    game: Game,
    store: Store,
    reference: dict[str, dict[str, Any]],
    turn: Turn,
) -> str:
    agent_id = agent["id"]
    if name == "act":
        cmd = str(arguments.get("cmd") or "")
        args = arguments.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        if cmd in ("hello", "join", "account.logout", "account.password", "account.email"):
            return "Эта команда делается за тебя системой; выбери другое действие."
        try:
            answer = await game.act(cmd, args)
        except Refused as refusal:
            store.event(agent_id, "refused", cmd=cmd, request=args, text=str(refusal))
            turn.actions.append((cmd, json.dumps(args, sort_keys=True), False))
            return f"ОТКАЗ: {refusal}"
        except GameError as trouble:
            store.event(agent_id, "error", cmd=cmd, request=args, text=str(trouble))
            turn.finished = True
            return f"Сбой связи с сервером: {trouble}. Ход окончен."
        store.event(agent_id, "action", cmd=cmd, request=args, reply=shrink(answer))
        turn.actions.append((cmd, json.dumps(args, sort_keys=True), True))
        return pack(answer)
    if name == "help":
        return commands.help_text(reference, str(arguments.get("cmd") or ""))
    if name == "read":
        path = str(arguments.get("path") or "").strip("/")
        try:
            return pack(await game.public(path))
        except Exception as trouble:  # noqa: BLE001 -- the model gets the text, the log the rest
            return f"Не прочиталось: {trouble}"
    if name == "remember":
        notes = str(arguments.get("notes") or "")[:MAX_NOTES_CHARS]
        store.update_agent(agent_id, {"notes": notes})
        agent["notes"] = notes
        return "Заметки обновлены."
    if name == "report_bug":
        text = str(arguments.get("text") or "").strip()
        if text:
            context = store.recent(agent_id, ("action", "refused"), 6)
            store.report(agent_id, text, context)
            store.event(agent_id, "bug", text=text)
        return "Записано."
    if name == "finish":
        turn.thought = str(arguments.get("thought") or "").strip()
        turn.finished = True
        return "Ход окончен."
    return f"Нет такого инструмента: {name}"
