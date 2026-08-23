# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the model is shown of `look`: a short digest plus what changed.

The raw `look` is the browser's window into the world and weighs a couple of
thousand characters; most of it is the same turn after turn. The model gets
the constant part as a digest (money, place, ways out, body, occupations, the
sack in one line), the rest as a diff against the previous turn, and the whole
thing only every few turns or when the diff would not be shorter. The raw
reply is always in the journal, and `act("look")` still returns it whole.

Since D-226 `look` carries the live part alone: recipes, orders, batches and
deeds moved to `knowledge`, `orders`, `deeds`, `shelf`, and the stations of a
place are the things standing in it (`bench`). The digest follows that shape.
"""

from __future__ import annotations

import json
from typing import Any

#: Keys that change every turn by themselves and would make every diff noisy.
VOLATILE = {"clock"}
#: How many turns between two full views, at most.
FULL_EVERY = 5
#: Items named in the one-line sack, and ways out named in the one line.
SACK_ITEMS = 15
EXITS = 10


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
    sleeping = "спит" if body.get("sleeping_since") else "бодрствует"
    first = (
        f"Ты: {look.get('identity')}, деньги {look.get('money')}, сила тела "
        f"{_num(body.get('stamina'))}, {sleeping}"
    )
    carry = look.get("carry") or {}
    if carry.get("capacity"):
        first += f", несёшь {_num(carry.get('load') or 0)}/{_num(carry['capacity'])} кг"
    lines.append(first + ".")
    lines.extend(_place(look))
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


def _place(look: dict[str, Any]) -> list[str]:
    """Where the body stands and what of it matters for acting.

    Work stations are things standing here (`bench`) rather than a field of
    the node, and the ways out are `exits` -- without them in the digest the
    agent has to read the whole `look` just to learn where it may walk.
    """
    node = look.get("node") or {}
    lines = [f"Место: {node.get('name')} [{node.get('key')}]"]
    marks = []
    if node.get("owner_city"):
        marks.append(f"город {node['owner_city']}")
    elif node.get("owner"):
        marks.append(f"владелец {node['owner']}")
    if node.get("features"):
        marks.append("есть: " + ", ".join(map(str, node["features"][:8])))
    if node.get("gated"):
        marks.append("вход закрыт")
    if node.get("mine"):
        marks.append("шахта")
    if marks:
        lines[0] += " — " + "; ".join(marks)

    #: Whose the ground is, in words. `floor.mine` is the server's own answer
    #: to "may I build here"; without it in the digest an agent looked for a
    #: way to own land that outside a city nobody owns (D-198) and searched
    #: for a place to found a city turn after turn (agents' finding).
    floor = look.get("floor") or {}
    if not node.get("owner") and not node.get("owner_city"):
        lines.append(
            "Участок ничей"
            + (": строить и ставить оборудование здесь можно." if floor.get("mine") else ".")
        )
    elif floor.get("mine"):
        lines.append("Участок твой: строить и ставить оборудование здесь можно.")

    bench = [b for b in look.get("bench") or [] if isinstance(b, dict)]
    if bench:
        lines.append(
            "Станции здесь: "
            + ", ".join(
                f"{b.get('goods')}{' (занята)' if b.get('busy') else ''}"
                + (" — твоя" if b.get("mine") else "")
                for b in bench[:8]
            )
        )
    veins = [v for v in look.get("veins") or [] if isinstance(v, dict)]
    if veins:
        lines.append(
            "Жилы здесь: "
            + ", ".join(
                f"{v.get('resource')} (богатство {_num(v.get('richness'))}, id {v.get('id')})"
                for v in veins[:6]
            )
        )
    here = []
    for key, label in (
        ("stall", "на прилавке позиций"),
        ("storages", "хранилищ"),
        ("vehicles", "транспорта"),
        ("furniture", "мебели"),
    ):
        value = look.get(key)
        if isinstance(value, list) and value:
            here.append(f"{label}: {len(value)}")
    if here:
        lines.append("Здесь же — " + "; ".join(here) + ".")

    exits = [e for e in look.get("exits") or [] if isinstance(e, dict)]
    if exits:
        named = [
            f"{e.get('name')} [{e.get('key')}] {_num(e.get('seconds'))}с" for e in exits[:EXITS]
        ]
        more = f" …и ещё {len(exits) - EXITS}" if len(exits) > EXITS else ""
        lines.append("Выходы: " + "; ".join(named) + more)

    city = look.get("city")
    if isinstance(city, dict) and city.get("name"):
        who = "ты гражданин" if city.get("citizen") else "ты не гражданин"
        line = f"Город здесь: {city['name']} [{city.get('node')}] — {who}"
        if city.get("admission"):
            line += f", приём: {city['admission']}"
        if city.get("requested"):
            line += ", заявка подана"
        if city.get("powers"):
            line += ", твои полномочия: " + ", ".join(map(str, city["powers"][:6]))
        lines.append(line)
    return lines


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
