# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the model is shown of `look`: a short digest plus what changed.

The raw `look` is the browser's window into the world and weighs up to nine
thousand characters; most of it is the same turn after turn. The model gets
the constant part as a digest (money, place, body, occupations, the sack in
one line), the rest as a diff against the previous turn, and the whole thing
only every few turns or when the diff would not be shorter. The raw reply is
always in the journal, and `act("look")` still returns it whole on demand.
"""

from __future__ import annotations

import json
from typing import Any

#: Keys that change every turn by themselves and would make every diff noisy.
VOLATILE = {"clock"}
#: How many turns between two full views, at most.
FULL_EVERY = 5
#: Items named in the one-line sack.
SACK_ITEMS = 15


def _look(seen: dict[str, Any]) -> dict[str, Any]:
    return seen.get("look") if isinstance(seen.get("look"), dict) else seen


def digest(seen: dict[str, Any]) -> str:
    """The constant part, short: who, how much, where, what the body is up to."""
    look = _look(seen)
    if "refused" in seen and "look" not in seen:
        return f"look отказан: {seen['refused']}"
    lines: list[str] = []
    body = look.get("body")
    if body is None:
        lines.append(
            f"Ты: {look.get('identity')}, деньги {look.get('money')}. ТЕЛА НЕТ — ты в облаке."
        )
        printing = look.get("printing")
        if printing:
            lines.append(f"Печатается тело, готово к {printing.get('ready_at')}.")
        else:
            lines.append("Чтобы вернуться в мир, закажи печать тела (body.printers / body.print).")
        return "\n".join(lines)
    node = look.get("node") or {}
    sleeping = "спит" if body.get("sleeping_since") else "бодрствует"
    lines.append(
        f"Ты: {look.get('identity')}, деньги {look.get('money')}, сила тела "
        f"{_num(body.get('stamina'))}, {sleeping}."
    )
    place = f"Место: {node.get('name')} [{node.get('key')}]"
    extras = []
    if node.get("stations"):
        extras.append("станции: " + ", ".join(map(str, node["stations"][:8])))
    if node.get("features"):
        extras.append("есть: " + ", ".join(map(str, node["features"][:8])))
    if node.get("owner_city"):
        extras.append(f"город {node['owner_city']}")
    elif node.get("owner"):
        extras.append(f"владелец {node['owner']}")
    if node.get("mine"):
        extras.append("шахта")
    lines.append(place + (" — " + "; ".join(extras) if extras else ""))
    city = look.get("city")
    if isinstance(city, dict) and city.get("name"):
        lines.append(f"Твой город: {city.get('name')} (гражданство: {look.get('citizenship')})")
    travel = look.get("travel")
    if isinstance(travel, dict):
        lines.append(
            f"В ПУТИ в {travel.get('final') or travel.get('to')}, прибытие {travel.get('arrives_at')}."
        )
    doings = [d for d in look.get("doings") or [] if isinstance(d, dict)]
    if doings:
        lines.append(
            "Дела: "
            + "; ".join(f"{d.get('title') or d.get('kind')} до {d.get('until')}" for d in doings)
        )
    items = [i for i in look.get("inventory") or [] if isinstance(i, dict)]
    if items:
        named = [f"{i.get('goods')}×{_num(i.get('amount'))}" for i in items[:SACK_ITEMS]]
        more = f" …и ещё {len(items) - SACK_ITEMS}" if len(items) > SACK_ITEMS else ""
        lines.append(f"Сумка ({len(items)}): " + ", ".join(named) + more)
    else:
        lines.append("Сумка пуста.")
    #: Recipes, orders and batches are not in `look` any more (D-226, step 2):
    #: the commands `knowledge` and `orders` read them when the model asks.
    counts = []
    if look.get("net_unread"):
        counts.append(f"непрочитанного в Сети: {look['net_unread']}")
    if counts:
        lines.append("; ".join(counts) + ".")
    return "\n".join(lines)


def _num(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return str(value)


def diff(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[str]:
    """Human-readable changes between two looks, top-level key by key."""
    if previous is None:
        return []
    before, after = _look(previous), _look(current)
    changes: list[str] = []
    for key in sorted(set(before) | set(after)):
        if key in VOLATILE:
            continue
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        changes.extend(_describe(key, old, new))
    return changes


def _describe(path: str, old: Any, new: Any) -> list[str]:
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for key in sorted(set(old) | set(new)):
            if old.get(key) != new.get(key):
                out.extend(_describe(f"{path}.{key}", old.get(key), new.get(key)))
        return out
    if isinstance(old, list) and isinstance(new, list):
        before = {_fingerprint(x) for x in old}
        after = {_fingerprint(x) for x in new}
        gone = [x for x in old if _fingerprint(x) not in after]
        came = [x for x in new if _fingerprint(x) not in before]
        out = []
        if came:
            out.append(f"{path}: появилось {_short_list(came)}")
        if gone:
            out.append(f"{path}: исчезло {_short_list(gone)}")
        return out
    return [f"{path}: {_short(old)} → {_short(new)}"]


def _fingerprint(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _short_list(items: list[Any], limit: int = 6) -> str:
    named = []
    for item in items[:limit]:
        if isinstance(item, dict) and item.get("goods"):
            named.append(f"{item['goods']}×{_num(item.get('amount'))}")
        elif isinstance(item, dict) and (item.get("name") or item.get("title")):
            named.append(str(item.get("name") or item.get("title")))
        else:
            named.append(_short(item, 80))
    more = f" …и ещё {len(items) - limit}" if len(items) > limit else ""
    return ", ".join(named) + more


def observation(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    full: bool,
    packed: str,
) -> tuple[str, str]:
    """The text for the model and the mode it was built in (`full` / `delta`)."""
    if full or previous is None:
        return f"{digest(current)}\n\nПолный look:\n{packed}", "full"
    changes = diff(previous, current)
    body = "\n".join(f"- {line}" for line in changes) if changes else "- ничего не изменилось"
    text = f"{digest(current)}\n\nИзменилось с прошлого хода:\n{body}"
    if len(text) >= len(packed):
        return f"{digest(current)}\n\nПолный look:\n{packed}", "full"
    return text, "delta"


#: Keys of an event that are bookkeeping, not news.
EVENT_PLUMBING = {"event", "seq", "at", "touches"}


def happened(events: list[dict[str, Any]], limit: int = 20) -> str:
    """What the server said since the last turn (D-226), one line per event.

    The kind first, then who did it when it was not the agent, then what the
    teller added -- the name of a place, a recipe. Nothing is interpreted
    here: the model reads journal kinds as well as it reads anything.
    """
    if not events:
        return ""
    lines = []
    for happening in events[-limit:]:
        kind = str(happening.get("event", "?"))
        parts = [kind]
        who = happening.get("who")
        if who:
            parts.append(f"кто: {who}")
        for key, value in happening.items():
            if key in EVENT_PLUMBING or key == "who" or value in (None, "", [], {}):
                continue
            #: A line of room talk is another player's words: fenced as data.
            if key == "line" and isinstance(value, dict) and isinstance(value.get("text"), str):
                clean = value["text"].replace("⟦", "").replace("⟧", "")
                value = {**value, "text": f"⟦чужой текст: {clean}⟧"}
            parts.append(f"{key}: {_short(value, 160)}")
        lines.append("- " + " · ".join(parts))
    if len(events) > limit:
        lines.insert(0, f"- … ещё {len(events) - limit} раньше")
    return "\n".join(lines)
