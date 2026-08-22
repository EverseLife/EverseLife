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
from datetime import UTC, datetime
from typing import Any

from . import commands, llm, observe
from .game import Game, GameError, Refused
from .store import Store

log = logging.getLogger(__name__)

MAX_LIST = 25
MAX_STRING = 400
MAX_REPLY_CHARS = 9000
MAX_NOTES_CHARS = 4000
DEFAULT_HISTORY = 20
#: The longest the agent may ask to sleep: a day. Beyond that it is "off".
MAX_WAIT = 24 * 3600

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
            "name": "note_add",
            "description": (
                "Add one note to your memory (numbered entries shown every turn). "
                "Save what you consider important: plans, ids, lessons. Refused when "
                "memory is full -- then edit or delete old notes first."
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
            "name": "note_edit",
            "description": "Replace the text of note number `id` (as shown in your notes).",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["id", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "note_delete",
            "description": "Delete note number `id`. The others keep their numbers until the next turn.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "integer"}},
                "required": ["id"],
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
            "description": (
                "End this turn. Say in one or two sentences what you did and what is next. "
                "wait_seconds: ask to be woken up no earlier than this (e.g. when a batch is "
                "ready) instead of the usual cadence. While your body is busy (travel, survey, "
                "foraging) you are not woken up anyway."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {"type": "string"},
                    "wait_seconds": {"type": "integer", "minimum": 0},
                },
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
- Сначала посмотри, что ты видишь: «Наблюдение» — сводка по look и что изменилось с
  прошлого хода; полный look показывается раз в несколько ходов. Нужны подробности
  (весь инвентарь, известные рецепты, заказы) — вызови act("look") сам.
- Если не уверен в аргументах команды — вызови help. Отказ сервера — нормальная часть игры:
  прочитай причину и действуй иначе. Не повторяй одно и то же действие, если оно отказано.
- Публичные каталоги (двери, карта, рынки, рецепты) — через read.
- Если отказ противоречит правилам или мир ведёт себя невозможным образом — report_bug.
- У тебя есть заметки — память между ходами, пронумерованный список. Ты сам решаешь,
  что в них важно сохранить: план, найденные id, выводы. note_add добавляет запись,
  note_edit(id) переписывает одну, note_delete(id) удаляет. Место ограничено
  ({notes_limit} знаков); когда оно кончается, новые записи не принимаются — сократи
  или удали старые. Записывать каждый ход не обязательно: последние действия и
  рассуждения ты и так увидишь в следующем ходе.
- Закончи ход вызовом finish, когда сделал, что хотел, или решил подождать. Пока тело
  занято (путь, разведка, сбор), тебя не будят — ждать вручную не нужно. Если ждёшь
  чего-то другого (партия, постройка), скажи в finish, через сколько секунд тебя разбудить.
- Ходов немного: за один ход не больше {max_steps} вызовов инструментов.

Команды сессии (имя(аргументы): что делает):
{reference}
"""


def _parse_json(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


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
    #: The agent's own wish: wake me no earlier than this many seconds from now.
    wait_seconds: int = 0
    #: What the world says: the body is busy until this moment (UTC).
    busy_until: datetime | None = None


def _history(store: Store, agent_id: str, limit: int) -> str:
    lines = []
    if limit <= 0:
        return "(история отключена)"
    kinds = ("action", "refused", "thought", "bug", "model")
    for event in store.recent(agent_id, kinds, limit):
        if event["kind"] == "model":
            if event["text"]:
                lines.append(f"[{event['at']}] рассуждение: {event['text'][:600]}")
        elif event["kind"] == "thought":
            lines.append(f"[{event['at']}] итог хода: {event['text']}")
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
    history = int(agent.get("history_limit") or DEFAULT_HISTORY)

    try:
        seen = await game.act("look")
    except Refused as refusal:
        seen = {"refused": str(refusal)}
    #: The previous look, and how long since the model last saw one whole: the
    #: observation is a digest plus a diff, the whole thing every few turns.
    previous_looks = store.recent(agent_id, ("look",), observe.FULL_EVERY)
    previous = _parse_json(previous_looks[-1]["reply"]) if previous_looks else None
    full = len(previous_looks) < observe.FULL_EVERY or not any(
        e["text"] == "full" for e in previous_looks
    )
    observation, mode = observe.observation(previous, shrink(seen), full=full, packed=pack(seen))
    store.event(agent_id, "look", cmd="look", reply=shrink(seen), text=mode)

    system = SYSTEM.format(
        name=agent["name"],
        persona=agent["persona"] or "спокойный, практичный, любопытный",
        goal=agent["goal"] or "жить, зарабатывать и обустраиваться",
        max_steps=max_steps,
        notes_limit=MAX_NOTES_CHARS,
        reference=commands.brief(reference),
    )
    notes = render_notes(agent["notes"])
    recent = _history(store, agent_id, history)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Твои заметки ({len(agent['notes'])}/{MAX_NOTES_CHARS} зн.):\n{notes}\n\n"
                f"Последние действия и рассуждения:\n{recent}\n\n"
                f"Наблюдение:\n{observation}\n\n"
                "Твой ход."
            ),
        },
    ]
    #: Where the prompt's weight is, in characters: the system part is the
    #: same every turn (and cached by the provider), the rest is per turn.
    store.event(
        agent_id,
        "prompt",
        text=(
            f"промпт: системная часть {len(system)} зн. (из них справочник команд "
            f"{len(commands.brief(reference))}), заметки {len(notes)}, история {len(recent)} "
            f"({history} записей), наблюдение {len(observation)} ({mode})"
        ),
        #: The exact text, so "what did the model actually see" has an answer.
        reply={"system": system, "user": messages[1]["content"]},
    )

    while turn.steps < max_steps and not turn.finished:
        reply = await llm.chat(provider, messages, TOOLS, model=agent["model"] or "")
        turn.prompt_tokens += reply.prompt_tokens
        turn.completion_tokens += reply.completion_tokens
        store.add_usage(agent_id, reply.prompt_tokens, reply.completion_tokens)
        messages.append(reply.raw_message)
        #: Every call to the model is an event: what it thought, what it asked
        #: for, and what that cost. Where the tokens go is visible per call.
        store.event(
            agent_id,
            "model",
            text="\n".join(part for part in (reply.reasoning, reply.content) if part).strip(),
            reply={
                "prompt_tokens": reply.prompt_tokens,
                "completion_tokens": reply.completion_tokens,
                "prompt_chars": sum(len(str(m.get("content") or "")) for m in messages),
                "tool_calls": [_call_summary(call) for call in reply.tool_calls],
            },
        )

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
    try:
        turn.busy_until = busy_until(await game.act("look"))
    except (Refused, GameError):
        turn.busy_until = None
    return turn


def busy_until(seen: dict[str, Any]) -> datetime | None:
    """When the body is free again, by the world's own clock: the latest of the
    running occupations and the journey under way. None when it is free now."""
    look = seen.get("look") or seen
    stamps: list[str] = []
    travel = look.get("travel")
    if isinstance(travel, dict) and travel.get("arrives_at"):
        stamps.append(travel["arrives_at"])
    for doing in look.get("doings") or []:
        if isinstance(doing, dict) and doing.get("until"):
            stamps.append(doing["until"])
    printing = look.get("printing")
    if isinstance(printing, dict) and printing.get("ready_at"):
        stamps.append(printing["ready_at"])
    latest: datetime | None = None
    for stamp in stamps:
        try:
            moment = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        if latest is None or moment > latest:
            latest = moment
    if latest is None or latest <= datetime.now(UTC):
        return None
    return latest


def _call_summary(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function") or {}
    try:
        args = json.loads(function.get("arguments") or "{}")
    except json.JSONDecodeError:
        args = function.get("arguments")
    return {"name": function.get("name"), "args": args}


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
            #: The socket dropped on this command. Come back on a new one and
            #: let the model decide what to do about it; only a second failure
            #: ends the turn.
            store.event(agent_id, "error", cmd=cmd, request=args, text=str(trouble))
            try:
                await game.reconnect()
            except (GameError, Refused) as again:
                store.event(agent_id, "error", text=f"переподключиться не удалось: {again}")
                turn.finished = True
                return f"Сбой связи с сервером: {trouble}. Ход окончен."
            return (
                f"Связь с сервером оборвалась на команде {cmd} ({trouble}) и восстановлена. "
                "Команда, скорее всего, не выполнена; проверь состояние (look) прежде чем "
                "повторять, и если обрыв повторится на той же команде — report_bug."
            )
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
    if name in ("note_add", "note_edit", "note_delete"):
        return _note_tool(name, arguments, agent=agent, store=store)
    if name == "report_bug":
        text = str(arguments.get("text") or "").strip()
        if text:
            context = store.recent(agent_id, ("action", "refused"), 6)
            store.report(agent_id, text, context)
            store.event(agent_id, "bug", text=text)
        return "Записано."
    if name == "finish":
        turn.thought = str(arguments.get("thought") or "").strip()
        try:
            turn.wait_seconds = max(0, min(int(arguments.get("wait_seconds") or 0), MAX_WAIT))
        except (TypeError, ValueError):
            turn.wait_seconds = 0
        turn.finished = True
        return "Ход окончен."
    return f"Нет такого инструмента: {name}"


# --- notes: a numbered list the agent edits entry by entry -----------------------


def split_notes(raw: str) -> list[str]:
    return [line for line in (raw or "").split("\n") if line.strip()]


def join_notes(entries: list[str]) -> str:
    return "\n".join(entries)


def render_notes(raw: str) -> str:
    entries = split_notes(raw)
    if not entries:
        return "(пусто)"
    return "\n".join(f"#{i} {text}" for i, text in enumerate(entries, 1))


def _note_tool(name: str, arguments: dict[str, Any], *, agent: dict[str, Any], store: Store) -> str:
    entries = split_notes(agent["notes"])
    #: One entry is one line: a newline inside would break the numbering.
    text = " ".join(str(arguments.get("text") or "").split())
    if name == "note_delete" or name == "note_edit":
        try:
            index = int(arguments.get("id"))
        except (TypeError, ValueError):
            return "Нужен номер записи (id)."
        if not 1 <= index <= len(entries):
            return f"Нет записи #{index}: записей {len(entries)}."
    if name == "note_add":
        if not text:
            return "Пустую запись не добавляю."
        candidate = join_notes([*entries, text])
        if len(candidate) > MAX_NOTES_CHARS:
            return (
                f"Память заполнена: {len(agent['notes'])}/{MAX_NOTES_CHARS} зн., новой записи "
                f"нужно ещё {len(candidate) - MAX_NOTES_CHARS}. Сократи (note_edit) или удали "
                "(note_delete) старые записи."
            )
        entries.append(text)
        outcome = f"Записано как #{len(entries)}."
    elif name == "note_edit":
        if not text:
            return "Пустой текст: чтобы убрать запись, вызови note_delete."
        candidate = join_notes([*entries[: index - 1], text, *entries[index:]])
        if len(candidate) > MAX_NOTES_CHARS:
            return (
                f"Так память переполнится ({len(candidate)}/{MAX_NOTES_CHARS} зн.); сократи текст."
            )
        entries[index - 1] = text
        outcome = f"Запись #{index} заменена."
    else:
        entries.pop(index - 1)
        outcome = f"Запись #{index} удалена; остальные перенумерованы."
    notes = join_notes(entries)
    store.update_agent(agent["id"], {"notes": notes})
    agent["notes"] = notes
    return f"{outcome} Занято {len(notes)}/{MAX_NOTES_CHARS} зн."
