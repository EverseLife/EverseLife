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
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .names import label

#: Keys that change every turn by themselves and would make every diff noisy.
VOLATILE = {"clock"}
#: How many turns between two full views, at most.
FULL_EVERY = 5
#: Seconds in an hour: the heat reserve is given in hours from a stamp.
SECONDS_PER_HOUR = 3600
#: Items named in the one-line sack, and ways out named in the one line.
SACK_ITEMS = 15
EXITS = 10
#: Ten-thousandths of a coin (`src/units.MONEY_SCALE`). `look` gives the purse
#: in coins, while every price -- in the book, in an offer, in an argument of
#: `market.buy` -- is in these. The digest says both numbers, because a model
#: that sees a purse of 25 next to a price of 30000 buys what it cannot pay
#: for: that was 56 of 194 refusals in the agents' journal.
MONEY_SCALE = 10_000
#: Own orders, reservations and batches named in the standing block.
STANDING_ROWS = 8


def _look(seen: dict[str, Any]) -> dict[str, Any]:
    return seen.get("look") if isinstance(seen.get("look"), dict) else seen


def money(purse: Any) -> str:
    """The purse in both units at once -- coins, as `look` gives it, and the
    same sum in the units every price is quoted in."""
    try:
        minor = int(Decimal(str(purse)) * MONEY_SCALE)
    except (ArithmeticError, TypeError, ValueError):
        return f"деньги {purse}"
    return f"деньги {purse} монет (в ценах команд это {minor})"


#: Why a batch is not moving, in the server's own words. `away` is the one
#: that matters most: the work is frozen until the master comes back to the
#: machine, and "ждёт очереди" would send the agent on waiting instead of
#: walking.
WAITING = {
    "away": "стоит: тебя нет у станка в {node}",
    "queued": "ждёт очереди за другой твоей работой",
    "no_station": "негде делать: свободного станка здесь нет",
}


def _more(rows: list[Any]) -> str:
    return f" …и ещё {len(rows) - STANDING_ROWS}" if len(rows) > STANDING_ROWS else ""


def _batch(batch: dict[str, Any]) -> str:
    said = f"{label('goods', batch.get('output'))} ×{_num(batch.get('units'))}"
    if batch.get("ready_at"):
        return f"{said} готово к {batch['ready_at']}"
    why = WAITING.get(str(batch.get("waiting") or ""), "ждёт")
    return f"{said} — {why.format(node=batch.get('node') or 'том узле')}"


def _terminal(look: dict[str, Any], orders: list[dict[str, Any]]) -> list[str]:
    """Own goods on the terminal shelf here, and how much of them is free.

    The shelf is `look.stall`; what is committed is one's own sell orders in
    *this* node, which is why an order carries its node. `market.sell` refuses
    on the free amount, and without this line the agent learns it only by being
    refused: 26 of 38 refusals in ten minutes were «в терминале свободно 0».
    """
    stall = [thing for thing in look.get("stall") or [] if isinstance(thing, dict)]
    if not stall:
        return []
    here = (look.get("node") or {}).get("key")
    sells = [order for order in orders if order.get("side") == "sell"]
    #: An older server does not say which node an order stands in. Then every
    #: sell order counts against this shelf: too little free is a wasted plan,
    #: too much free is a refusal, and the shelf that looks all free is what
    #: sent the agent into `market.take` eighteen times in ten minutes.
    located = all("node_key" in order for order in sells)
    committed: dict[tuple[str, str], float] = {}
    for order in sells:
        if located and order.get("node_key") != here:
            continue
        spot = (str(order.get("goods")), str(order.get("tier")))
        committed[spot] = committed.get(spot, 0.0) + float(order.get("left") or 0)
    said = []
    for thing in stall[:STANDING_ROWS]:
        have = float(thing.get("amount") or 0)
        held = committed.get((str(thing.get("goods")), str(thing.get("tier"))), 0.0)
        free = max(0.0, have - held)
        line = (
            f"«{label('goods', thing.get('goods'))}» ({label('tiers', thing.get('tier'))})"
            f" ×{_num(have)}"
        )
        if held:
            line += f", свободно {'' if located else 'не больше '}{_num(free)}"
        said.append(line)
    return [
        "В терминале здесь твоё: "
        + "; ".join(said)
        + _more(stall)
        + ". Продать можно только свободное; забрать в сумку — market.take."
    ]


def standing(seen: dict[str, Any], look: dict[str, Any] | None = None) -> str:
    """Own orders, reservations, batches and the terminal shelf, in one block.

    None of it is in `look` (D-226) except the shelf, which is there but as a
    bare count: the agent has to ask `orders` for the rest. An agent that does
    not ask waits for a delivery it never ordered, posts the same order every
    turn and sells what is already committed -- all three in the journal.
    """
    own = seen.get("orders") if isinstance(seen.get("orders"), dict) else seen
    lines: list[str] = []
    orders = [o for o in own.get("orders") or [] if isinstance(o, dict)]
    if look:
        lines.extend(_terminal(_look(look), orders))
    if orders:
        lines.append(
            "Твои заявки на рынке: "
            + "; ".join(
                f"{'покупка' if o.get('side') == 'buy' else 'продажа'} «{label('goods', o.get('goods'))}»"
                f" ×{_num(o.get('left'))} по {o.get('price')} [{o.get('id')}]"
                for o in orders[:STANDING_ROWS]
            )
            + _more(orders)
            + ". Свою заявку не выкупают: она исполнится сама, когда сойдётся встречная."
        )
    held = [r for r in own.get("reservations") or [] if isinstance(r, dict)]
    if held:
        lines.append(
            "Твои брони: "
            + "; ".join(
                f"«{label('goods', r.get('goods'))}» ×{_num(r.get('amount'))} в {r.get('node')} до"
                f" {r.get('expires_at')} [{r.get('id')}]"
                for r in held[:STANDING_ROWS]
            )
            + _more(held)
            + ". Бронь забирают через market.redeem."
        )
    batches = [b for b in own.get("batches") or [] if isinstance(b, dict)]
    if batches:
        lines.append(
            "Твои партии: " + "; ".join(map(_batch, batches[:STANDING_ROWS])) + _more(batches)
        )
    if not lines:
        return "Ни заявок, ни броней, ни партий, ни товара в терминале — ждать нечего."
    return "\n".join(lines)


def digest(seen: dict[str, Any]) -> str:
    """The constant part, short: who, how much, where, what the body is up to."""
    look = _look(seen)
    if "refused" in seen and "look" not in seen:
        return f"look отказан: {seen['refused']}"
    lines: list[str] = []
    body = look.get("body")
    if body is None:
        lines.append(
            f"Ты: {look.get('identity')}, {money(look.get('money'))}. ТЕЛА НЕТ — ты в облаке."
        )
        printing = look.get("printing")
        if printing:
            lines.append(f"Печатается тело, готово к {printing.get('ready_at')}.")
        else:
            lines.append("Чтобы вернуться в мир, закажи печать тела (body.printers / body.print).")
        return "\n".join(lines)
    sleeping = "спит" if body.get("sleeping_since") else "бодрствует"
    first = (
        f"Ты: {look.get('identity')}, {money(look.get('money'))}, сила тела "
        f"{_num(body.get('stamina'))}, {sleeping}"
    )
    carry = look.get("carry") or {}
    if carry.get("capacity"):
        first += f", несёшь {_num(carry.get('load') or 0)}/{_num(carry['capacity'])} кг"
    lines.append(first + ".")
    #: The cold, where there is any (D-231). Said in the digest rather than left
    #: for the model to dig out of the raw `look`: a body freezes to death by
    #: not noticing, and an agent that has to ask for the hours never asks.
    lines.extend(_frost(look))
    #: And the ground about to move (D-197, P6). Same reason as the cold: an
    #: agent that has to dig the announced hour out of the raw `look` never
    #: digs, and the window to walk out is the whole licence for the burning.
    lines.extend(_shaking(look))
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
        named = [
            f"{label('goods', i.get('goods'))}×{_num(i.get('amount'))}" for i in items[:SACK_ITEMS]
        ]
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


def _shaking(look: dict[str, Any]) -> list[str]:
    """The announced hour of an eruption, while the window is open.

    What follows from it is spelled out, because the digest is the only thing
    the agent reliably reads: what lies on the ground burns, the ways out are
    redrawn, and a way breaking under somebody walking it kills them with
    everything they carry.
    """
    when = (look.get("node") or {}).get("shaking_at")
    if not when:
        return []
    return [
        (
            f"ЗЕМЛЯ ТРОНЕТСЯ здесь в {when}: лежащее на земле сгорит, дороги перечертит, "
            "а порвавшаяся под идущим убивает вместе с сумкой. Унести вещи и уйти — "
            "или улететь: под кораблём земля не двигается."
        )
    ]


def _frost(look: dict[str, Any]) -> list[str]:
    """The heat reserve in words: how long is left and what happens at zero.

    The server names the hours as of a stamp and the rate they move at, so that
    a client can draw the hand without asking again (D-226). The digest is that
    same arithmetic, done once for the turn.
    """
    frost = look.get("frost")
    if not isinstance(frost, dict):
        return []
    hours = _reserve(frost)
    climate = CLIMATE.get(str(frost.get("climate")), str(frost.get("climate")))
    where = "узел обогрет" if frost.get("warm") else f"здесь {climate}"
    if hours <= 0:
        alarm = (
            f"ЗАМЁРЗ ({where}): выносливость горит просто на времени, работа дороже, "
            "кончится — смерть. Грелка (frost.warm) или тёплый узел. Числа — "
            "в frost.* каталога констант."
        )
        return [alarm]
    trend = "восполняется" if frost.get("warm") else "тает"
    return [f"Тепло: {_num(round(hours, 1))} ч из {_num(frost.get('max'))}, {trend} ({where})."]


#: `frost.climate` is a two-word wire enum (D-251), not a renames domain: the
#: digest says it in the game's language itself.
CLIMATE = {"frost": "мороз", "heat": "жара"}


def _reserve(frost: dict[str, Any]) -> float:
    """What the reserve is now: what it was at the stamp, moved by the rate."""
    try:
        stamp = datetime.fromisoformat(str(frost.get("at")))
    except ValueError:
        return float(frost.get("hours") or 0)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    hours = (datetime.now(UTC) - stamp).total_seconds() / SECONDS_PER_HOUR
    moved = float(frost.get("hours") or 0) + float(frost.get("per_hour") or 0) * hours
    return max(0.0, min(float(frost.get("max") or 0), moved))


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
        marks.append(
            "есть: " + ", ".join(label("node_properties", f) for f in node["features"][:8])
        )
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
                f"{label('goods', b.get('goods'))}{' (занята)' if b.get('busy') else ''}"
                + (" — твоя" if b.get("mine") else "")
                for b in bench[:8]
            )
        )
    veins = [v for v in look.get("veins") or [] if isinstance(v, dict)]
    if veins:
        lines.append(
            "Жилы здесь: "
            + ", ".join(
                f"{label('goods', v.get('resource'))} (богатство {_num(v.get('richness'))}, id {v.get('id')})"
                for v in veins[:6]
            )
        )
    here = []
    #: `stall` is not counted here: it is one's own shelf in the terminal, and
    #: it is said by name in the standing block, with the free part.
    for key, word in (
        ("storages", "хранилищ"),
        ("vehicles", "транспорта"),
        ("furniture", "мебели"),
    ):
        value = look.get(key)
        if isinstance(value, list) and value:
            here.append(f"{word}: {len(value)}")
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
            named.append(f"{label('goods', item['goods'])}×{_num(item.get('amount'))}")
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
#: Event values that name a thing by its wire id (D-251): shown as «Имя [id]»,
#: like everywhere else in the observation.
EVENT_GOODS_KEYS = {"key", "goods", "output", "resource"}


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
            if key in EVENT_GOODS_KEYS and isinstance(value, str):
                value = label("goods", value)
            parts.append(f"{key}: {_short(value, 160)}")
        lines.append("- " + " · ".join(parts))
    if len(events) > limit:
        lines.insert(0, f"- … ещё {len(events) - limit} раньше")
    return "\n".join(lines)
