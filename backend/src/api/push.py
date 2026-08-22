# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The server speaks first: events from the journal go to the players they
concern (D-226, 08-session-protocol).

The journal is the source. Every change of the world is a row in `event`,
written in the same transaction as its consequences -- by the API process and
by the worker alike. A trigger on the table sends `NOTIFY event, <id>` with
the commit; this module listens in the API process, reads the row, and tells
whoever may see it.

**Notify is the alarm, the table is the truth.** A lost notification is not a
lost event: the hub keeps the last id it delivered and, when woken, reads
everything after it in order. That is also how a reconnecting client catches
up: `hello` with `since` replays the rows it missed through the same tellers.

What is not an event of the journal but must still reach the screen -- a line
of room talk, which the journal does not keep (D-070) -- goes through
`events.announce()`: a notification on the `touch` channel naming whom it
concerns and what parts of their state it changes, and nothing more.

A message to the client has the shape

    {"event": "knowledge.learned", "seq": 184213, "at": "...",
     "touches": ["knowledge"], ...what the teller adds}

`touches` is the promise every event keeps even without a teller: the client
knows what to read again. Tellers add what the recipient could have seen by
asking -- never the journal's innards.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import engine, session_factory
from src.models.event import Event
from src.models.identity import Identity
from src.models.world import Node

log = logging.getLogger(__name__)

#: How far back `hello` with `since` reaches. Older than this the client reads
#: its state whole -- replaying a week of a busy player would cost more than
#: the reads it saves.
REPLAY_HORIZON = timedelta(days=1)
#: Cap on a single replay. Beyond it the client is told to reread.
REPLAY_LIMIT = 500
#: The fallback sweep: without a notification the hub still looks at the
#: journal this often, so a missed `NOTIFY` costs seconds, not a session.
SWEEP_PERIOD = 5.0

#: Which parts of the player's state an event kind changes, by prefix of the
#: kind (`knowledge.learned` -> `knowledge`). The client rereads those parts.
#: A kind without a prefix here changes nothing the client caches -- it is
#: still delivered, with empty `touches`.
TOUCHES: dict[str, tuple[str, ...]] = {
    "body": ("body",),
    "meal": ("body",),
    "knowledge": ("knowledge",),
    "item": ("inventory",),
    "gear": ("inventory", "body"),
    "mining": ("mining", "inventory"),
    "travel": ("body", "node"),
    "road": ("node",),
    "ship": ("node", "ships"),
    "transport": ("node", "inventory"),
    "craft": ("doings", "inventory", "orders"),
    "carrier": ("inventory",),
    "library": ("shelf",),
    "land": ("node", "deeds"),
    "deed": ("node", "deeds"),
    "building": ("node",),
    "farm": ("farm",),
    "energy": ("node",),
    "utility": ("node", "money"),
    "station": ("node",),
    "storage": ("node", "inventory"),
    "explore": ("doings", "node"),
    "forage": ("doings", "inventory"),
    "customs": ("body", "money"),
    "city": ("city",),
    "justice": ("justice",),
    "bank": ("money", "bank"),
    "identity": ("profile",),
    "market": ("orders", "market"),
    "money": ("money",),
}
#: Kinds with their own list, when the prefix rule is too coarse.
TOUCHES_BY_KIND: dict[str, tuple[str, ...]] = {
    "craft.invented": ("doings", "inventory", "knowledge", "orders"),
    "body.printed": ("body", "node", "inventory"),
    "body.died": ("body", "node", "inventory"),
}

#: What everybody standing in the node sees happen there, by prefix or kind.
#: Personal affairs -- a meal, a purchase, a lesson -- stay personal; what
#: changes the place or who is in it is public to the place.
NODE_VISIBLE_PREFIXES = frozenset(
    {
        "road",
        "ship",
        "building",
        "land",
        "station",
        "storage",
        "energy",
        "market",
        "farm",
        "transport",
    }
)
NODE_VISIBLE_KINDS = frozenset(
    {
        "body.printed",
        "body.died",
        "travel.started",
        "travel.arrived",
        "travel.cancelled",
        "item.dropped",
        "item.picked",
        "mining.collapsed",
        "explore.found",
        "utility.cut_off",
        "city.founded",
        "deed.offered",
        "deed.sold",
    }
)
#: What a bystander in the node rereads. The node itself, and the book for
#: trade: their own pocket did not change.
NODE_TOUCHES: dict[str, tuple[str, ...]] = {"market": ("market",), "ship": ("node", "ships")}

#: Payload keys that name a second party by identity id. Those get the event
#: as their own: the office appointed, the letter sent.
ADDRESSEE_KEYS = ("to_identity_id", "whom", "seller")


def touches_of(kind: str) -> tuple[str, ...]:
    return TOUCHES_BY_KIND.get(kind) or TOUCHES.get(kind.split(".", 1)[0], ())


@dataclass(eq=False)
class Sink:
    """One connected client. Where it is and how to reach it.

    `node_id` follows the body: set at `hello`, moved by the arrival events the
    sink itself receives. In transit the body is nowhere (D-107) and hears
    nothing of either end.
    """

    send_raw: Callable[[dict[str, Any]], Awaitable[None]]
    identity_id: uuid.UUID | None = None
    node_id: uuid.UUID | None = None
    #: Streaming is opt-in: `hello` with `since`. A client that reads answers
    #: by order -- the old one, the tests -- must not get an event in between.
    listening: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, message: dict[str, Any]) -> None:
        async with self.lock:
            await self.send_raw(message)
        if "event" in message:
            hub.tally.events += 1


Teller = Callable[[AsyncSession, Event, Sink, dict[str, Any]], Awaitable[dict[str, Any] | None]]
_TELLERS: dict[str, Teller] = {}


def teller(kind: str) -> Callable[[Teller], Teller]:
    """Register how an event kind is told to a recipient.

    The teller gets the event, the sink and the message assembled so far
    (`event`, `seq`, `at`, `touches`, and `who` for node events). It returns
    the message to send, or None to withhold it from this recipient -- that is
    where visibility finer than actor-and-node lives.
    """

    def wrap(fn: Teller) -> Teller:
        _TELLERS[kind] = fn
        return fn

    return wrap


class Tally:
    """What the socket did since the process started (step 4 of the plan):
    the poll is gone when `look` stops being the most answered command."""

    def __init__(self) -> None:
        self.answers: dict[str, int] = {}
        self.events = 0
        self.since = datetime.now(UTC)

    def answered(self, cmd: str) -> None:
        self.answers[cmd] = self.answers.get(cmd, 0) + 1

    def report(self, connections: int, listening: int) -> dict[str, Any]:
        hours = max((datetime.now(UTC) - self.since).total_seconds() / 3600, 1e-9)
        return {
            "connections": connections,
            "listening": listening,
            "since": self.since.isoformat(),
            "events_sent": self.events,
            "answers": dict(sorted(self.answers.items(), key=lambda kv: -kv[1])[:20]),
            #: The number the plan watches: how often one connection asks
            #: `look` in an hour. With the poll it was 700-1800; the goal
            #: is an order of magnitude less.
            "look_per_connection_hour": round(
                self.answers.get("look", 0) / hours / max(connections, 1), 1
            ),
        }


class Hub:
    """The process's connections and the journal's tail."""

    def __init__(self) -> None:
        self.sinks: set[Sink] = set()
        self.tally = Tally()
        self._last_id = 0
        #: Made in `start()`: an Event is bound to the loop it is made on, and
        #: the hub is made at import, before any loop exists.
        self._wake: asyncio.Event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._touches: list[dict[str, Any]] = []
        #: Ids named by notifications. A row can commit after a later one
        #: has been delivered -- two transactions do not finish in id order --
        #: so the mark alone would skip it; the announced ids are read by name.
        self._announced: set[int] = set()
        self._delivered: set[int] = set()

    def attach(self, sink: Sink) -> None:
        self.sinks.add(sink)

    def detach(self, sink: Sink) -> None:
        self.sinks.discard(sink)

    def report(self) -> dict[str, Any]:
        return self.tally.report(
            len(self.sinks), sum(1 for sink in self.sinks if sink.listening)
        )

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        self._wake = asyncio.Event()
        self._announced.clear()
        self._delivered.clear()
        self._touches.clear()
        async with session_factory()() as db:
            self._last_id = (await db.execute(select(func.max(Event.id)))).scalar() or 0
        self._task = asyncio.create_task(self._run(), name="push-hub")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self._listen()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- the listener must outlive any failure
                log.exception("push listener failed; reconnecting")
                await asyncio.sleep(SWEEP_PERIOD)

    async def _listen(self) -> None:
        loop = asyncio.get_running_loop()

        def on_notify(_conn: Any, _pid: int, channel: str, payload: str) -> None:
            if channel == "touch":
                with contextlib.suppress(ValueError):
                    self._touches.append(json.loads(payload))
            else:
                with contextlib.suppress(ValueError):
                    self._announced.add(int(payload))
            loop.call_soon_threadsafe(self._wake.set)

        async with engine().connect() as connection:
            raw = await connection.get_raw_connection()
            driver = raw.driver_connection
            await driver.add_listener("event", on_notify)
            await driver.add_listener("touch", on_notify)
            try:
                while True:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), timeout=SWEEP_PERIOD)
                    self._wake.clear()
                    await self.pump()
            finally:
                with contextlib.suppress(Exception):
                    await driver.remove_listener("event", on_notify)
                    await driver.remove_listener("touch", on_notify)

    # -- delivery ------------------------------------------------------------

    async def pump(self) -> None:
        """Deliver everything the journal holds after the last delivered row,
        and every touch that arrived. Safe to call at any time: it is what the
        listener does when woken, and what a test does instead of waiting."""
        touches, self._touches = self._touches, []
        announced, self._announced = self._announced, set()
        for note in touches:
            await self._deliver_touch(note)
        if not any(sink.listening for sink in self.sinks):
            #: Nobody to tell: just move the mark, so the next listener does
            #: not get the backlog of an empty room.
            async with session_factory()() as db:
                self._last_id = (
                    await db.execute(select(func.max(Event.id)))
                ).scalar() or self._last_id
            self._delivered.clear()
            return
        async with session_factory()() as db:
            wanted = Event.id > self._last_id
            if announced:
                wanted = wanted | Event.id.in_(announced)
            rows = (
                (await db.execute(select(Event).where(wanted).order_by(Event.id))).scalars().all()
            )
            for row in rows:
                if row.id in self._delivered:
                    continue
                await self._deliver(db, row, self.sinks)
                self._delivered.add(row.id)
                self._last_id = max(self._last_id, row.id)
            #: The delivered set only needs to cover what a late commit could
            #: still name: everything below the mark minus a window is history.
            if len(self._delivered) > 4096:
                floor = self._last_id - 1024
                self._delivered = {i for i in self._delivered if i > floor}

    async def replay(self, sink: Sink, since: int) -> None:
        """Catch a reconnected client up: what happened after `since` that it
        may see. Beyond the horizon or the cap the client is told to reread."""
        async with session_factory()() as db:
            horizon = datetime.now(UTC) - REPLAY_HORIZON
            rows = (
                (
                    await db.execute(
                        select(Event)
                        .where(Event.id > since, Event.at > horizon)
                        .order_by(Event.id)
                        .limit(REPLAY_LIMIT + 1)
                    )
                )
                .scalars()
                .all()
            )
            if len(rows) > REPLAY_LIMIT:
                await sink.send(
                    {"event": "session.reread", "seq": self._last_id, "touches": ["all"]}
                )
                return
            for row in rows:
                await self._deliver(db, row, {sink})

    async def _deliver(self, db: AsyncSession, row: Event, sinks: set[Sink]) -> None:
        kind = row.kind
        prefix = kind.split(".", 1)[0]
        own = touches_of(kind)
        public = prefix in NODE_VISIBLE_PREFIXES or kind in NODE_VISIBLE_KINDS
        parties = _parties(row)
        who: str | None = None

        for sink in list(sinks):
            if not sink.listening or sink.identity_id is None:
                continue
            personal = sink.identity_id in parties
            bystander = (
                not personal
                and public
                and row.node_id is not None
                and sink.node_id == row.node_id
            )
            if not personal and not bystander:
                continue
            message: dict[str, Any] = {
                "event": kind,
                "seq": row.id,
                "at": row.at.isoformat(),
                "touches": list(own if personal else NODE_TOUCHES.get(prefix, ("node",))),
            }
            if bystander or (personal and row.actor_identity_id != sink.identity_id):
                if who is None and row.actor_identity_id is not None:
                    actor = await db.get(Identity, row.actor_identity_id)
                    who = actor.name if actor else ""
                if who:
                    message["who"] = who
            tell = _TELLERS.get(kind)
            if tell is not None:
                told = await tell(db, row, sink, message)
                if told is None:
                    continue
                message = told
            #: The body moved: the sink follows it before anyone sends it the
            #: talk of the place it left.
            if personal and sink.identity_id == row.actor_identity_id:
                _follow(sink, row)
            await sink.send(message)

    async def _deliver_touch(self, note: dict[str, Any]) -> None:
        identity = _uuid(note.get("identity_id"))
        node = _uuid(note.get("node_id"))
        message = {
            "event": str(note.get("event") or "touch"),
            "touches": list(note.get("touches") or []),
        }
        if note.get("who"):
            message["who"] = note["who"]
        for sink in list(self.sinks):
            if not sink.listening:
                continue
            personal = identity is not None and sink.identity_id == identity
            in_room = identity is None and node is not None and sink.node_id == node
            if personal or in_room:
                await sink.send(message)


def _parties(row: Event) -> set[uuid.UUID]:
    parties: set[uuid.UUID] = set()
    if row.actor_identity_id is not None:
        parties.add(row.actor_identity_id)
    for key in ADDRESSEE_KEYS:
        found = _uuid((row.payload or {}).get(key))
        if found is not None:
            parties.add(found)
    return parties


def _follow(sink: Sink, row: Event) -> None:
    if row.kind in ("travel.arrived", "travel.cancelled", "body.printed"):
        sink.node_id = row.node_id
    elif row.kind in ("travel.started", "body.died"):
        sink.node_id = None


def _uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return uuid.UUID(value)
    return None


#: One hub per process.
hub = Hub()


# -- tellers ---------------------------------------------------------------


@teller("knowledge.learned")
async def _knowledge_learned(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    payload = row.payload or {}
    if payload.get("key"):
        message["key"] = payload["key"]
    if payload.get("name"):
        message["name"] = payload["name"]
    if payload.get("kind_of_knowledge"):
        message["kind"] = payload["kind_of_knowledge"]
    return message


async def _named_node(db: AsyncSession, row: Event, message: dict[str, Any]) -> dict[str, Any]:
    if row.node_id is not None:
        node = await db.get(Node, row.node_id)
        if node is not None:
            message["node"] = {"key": node.key, "name": node.name}
    return message


@teller("travel.arrived")
async def _travel_arrived(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    return await _named_node(db, row, message)


@teller("travel.started")
async def _travel_started(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    return await _named_node(db, row, message)


@teller("body.printed")
async def _body_printed(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    return await _named_node(db, row, message)


def _carry(row: Event, message: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Copy the named payload keys into the message -- what the recipient
    could have seen by asking. Ids stay in the journal."""
    payload = row.payload or {}
    for key in keys:
        if payload.get(key) is not None:
            message[key] = payload[key]
    return message


#: The book is public (D-047): a trade or an order in the node is told to
#: everyone in it with goods, tier, price and amount -- never with names.
@teller("market.trade")
async def _market_trade(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    message.pop("who", None)
    return _carry(row, message, "type_key", "tier", "price", "amount")


@teller("market.order_placed")
async def _market_order_placed(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    message.pop("who", None)
    return _carry(row, message, "side", "type_key", "tier", "price", "amount")


#: The face: every swing is told with what it brought; a collapse with what
#: it took. The miner's own numbers -- bystanders hear the collapse alone.
@teller("mining.swing")
async def _mining_swing(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    return _carry(row, message, "mined", "quality")


@teller("mining.collapsed")
async def _mining_collapsed(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    if sink.identity_id == row.actor_identity_id:
        return _carry(row, message, "lost", "wounded", "killed")
    return message
