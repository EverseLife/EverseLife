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
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from src.db.base import engine, session_factory
from src.engine import city as town
from src.models.event import Event
from src.models.identity import Identity
from src.models.world import Node


def _now() -> datetime:
    return datetime.now(UTC)


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
#: Messages a client may leave unread before it is cut off.
OUTBOX_LIMIT = 256
#: Journal rows read per pass of the pump: a tick that wrote thousands is
#: delivered in slices, not held in memory whole.
PUMP_BATCH = 500
#: How long a gap in the id run may stay open before the pump rules it a
#: rolled-back id and steps the watermark over it. Longer than any job's
#: transaction may live (`JOB_STATEMENT_TIMEOUT_MS`), with room to spare.
GAP_HORIZON = timedelta(minutes=20)

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
    #: The planet redrew the map around you (D-197): the ways out, what lies
    #: here and what the veins are have all just changed.
    "plates": ("node",),
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
    #: The works fund paid or the board changed (D-248): the wallet and the
    #: public bank numbers both move.
    "works": ("money", "bank"),
    #: The alpha's debug widget (D-229). Without a line here the widget would
    #: work only because the client rereads the world after any action of its
    #: own: a second tab of the same player would see nothing. The thing
    #: printed rides in the inventory, the pulled-up term in the doings.
    "alpha": ("inventory", "doings"),
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
        #: The signal before an eruption and the eruption itself (D-197). Free
        #: and to everybody standing in the node: it is the window to walk out
        #: of, and the whole licence for the burning and the deaths that follow.
        "plates",
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
#: as their own: the office appointed, the defendant, the seller. Any key
#: ending in `_identity_id` counts; the journal names parties by id, and the
#: teller turns ids into names (review 2026-08-23, wave 2).
ADDRESSEE_KEYS = ("to_identity_id", "seller")
ADDRESSEE_SUFFIX = "_identity_id"

#: What every citizen of the city hears, wherever they stand: the city's
#: affairs are theirs (D-160). The event names the city by `city_id`.
CITY_VISIBLE_PREFIXES = frozenset({"city", "justice"})
CITY_VISIBLE_KINDS = frozenset({"bank.rate_decided"})

#: Bystanders learn **who** only where the deed is in plain sight: somebody
#: arrived, fell, dropped a thing, was appointed. Trade, tax, fuel and
#: farming stay nameless to the room (D-047: the book trades goods, not
#: reputation). A teller may still add `who` for its kind.
NAMED_PREFIXES = frozenset({"travel", "body", "city", "justice"})
NAMED_KINDS = frozenset({"item.dropped", "item.picked", "mining.collapsed", "explore.found"})


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
    #: Closes the socket itself -- what a stopped reader gets.
    cut: Callable[[], Awaitable[None]] | None = None
    identity_id: uuid.UUID | None = None
    node_id: uuid.UUID | None = None
    #: Streaming is opt-in: `hello` with `since`. A client that reads answers
    #: by order -- the old one, the tests -- must not get an event in between.
    listening: bool = False
    #: Everything going out waits here, in order, for one writer task per
    #: socket: delivery to a thousand sinks must never block on one slow
    #: client's TCP window. A queue that fills up is a client that stopped
    #: reading -- the socket is closed and the client comes back with `since`.
    outbox: asyncio.Queue[dict[str, Any] | None] = field(
        default_factory=lambda: asyncio.Queue(maxsize=OUTBOX_LIMIT)
    )
    writer: asyncio.Task[None] | None = None
    cutting: asyncio.Task[None] | None = None
    closed: bool = False

    async def send(self, message: dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            self.outbox.put_nowait(message)
        except asyncio.QueueFull:
            log.warning(
                "sink %s stopped reading: %d messages queued", self.identity_id, OUTBOX_LIMIT
            )
            self.close()
            return
        if "event" in message:
            hub.tally.events += 1

    def close(self) -> None:
        """Stop writing and cut the socket. The writer ends on the sentinel,
        or is cancelled when the queue is too full to take one."""
        if self.closed:
            return
        self.closed = True
        try:
            self.outbox.put_nowait(None)
        except asyncio.QueueFull:
            if self.writer is not None:
                self.writer.cancel()
        if self.cut is not None:
            self.cutting = asyncio.get_running_loop().create_task(self._cut(), name="sink-cut")

    async def _cut(self) -> None:
        #: Closing a socket the other side already closed raises; that is the
        #: usual way out, not an error worth a traceback.
        with contextlib.suppress(Exception):
            await self.cut()  # type: ignore[misc]

    async def drain(self) -> None:
        """The writer: one message at a time, until closed or the socket fails."""
        try:
            while not self.closed:
                message = await self.outbox.get()
                if message is None:
                    return
                try:
                    await self.send_raw(message)
                except Exception:  # noqa: BLE001 -- a dead socket ends the writer, not the hub
                    log.info("sink %s gone while writing", self.identity_id)
                    return
        finally:
            self.closed = True
            hub.detach(self)


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
        self.by_identity: dict[uuid.UUID, set[Sink]] = {}
        self.by_node: dict[uuid.UUID, set[Sink]] = {}
        self.dirty = True
        self.tally = Tally()
        #: The watermark: every id at or below it has been delivered. It moves
        #: only along the unbroken run, so a row whose transaction took an id
        #: earlier and committed later is still read (`id > watermark`) and is
        #: never skipped.
        self._last_id = 0
        #: Ids delivered that sit above a gap in the run (later ids committed
        #: before an earlier one) -> the moment they carry. The watermark
        #: advances into this as the gaps close; a gap older than the horizon
        #: is a rolled-back id and is stepped over.
        self._ahead: dict[int, datetime] = {}
        #: Made in `start()`: an Event is bound to the loop it is made on, and
        #: the hub is made at import, before any loop exists.
        self._wake: asyncio.Event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._touches: list[dict[str, Any]] = []

    def attach(self, sink: Sink) -> None:
        self.sinks.add(sink)
        self.dirty = True
        if sink.writer is None:
            sink.writer = asyncio.create_task(sink.drain(), name="sink-writer")

    def detach(self, sink: Sink) -> None:
        self.sinks.discard(sink)
        self.dirty = True
        sink.close()

    def reindex(self) -> None:
        """Sinks by identity and by node, rebuilt when one attached, left or
        moved: delivery then asks for the few concerned, not all of them."""
        if not self.dirty:
            return
        by_identity: dict[uuid.UUID, set[Sink]] = {}
        by_node: dict[uuid.UUID, set[Sink]] = {}
        for sink in self.sinks:
            if not sink.listening:
                continue
            if sink.identity_id is not None:
                by_identity.setdefault(sink.identity_id, set()).add(sink)
            if sink.node_id is not None:
                by_node.setdefault(sink.node_id, set()).add(sink)
        self.by_identity, self.by_node = by_identity, by_node
        self.dirty = False

    def report(self) -> dict[str, Any]:
        return self.tally.report(len(self.sinks), sum(1 for sink in self.sinks if sink.listening))

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        self._wake = asyncio.Event()
        self._ahead.clear()
        self._touches.clear()
        async with session_factory()() as db:
            await self._settle(db)
        self._task = asyncio.create_task(self._run(), name="push-hub")

    async def stop(self) -> None:
        for sink in list(self.sinks):
            sink.close()
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
            #: The journal's notification carries nothing: it is a wake-up,
            #: one per transaction whatever it wrote.
            loop.call_soon_threadsafe(self._wake.set)

        #: Its own connection, outside the command pool: the listener holds
        #: one for the process's lifetime, and the pool's budget is the
        #: players served at once (review 2026-08-23).
        listener = create_async_engine(engine().url, poolclass=NullPool)
        async with listener.connect() as connection:
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
                await listener.dispose()

    # -- delivery ------------------------------------------------------------

    def _advance(self, now: datetime) -> None:
        """Move the watermark up the unbroken run of delivered ids, and step
        over a gap that is only a rolled-back id -- proven rolled back because
        a later id committed longer ago than any transaction may live.

        While a gap stays open, every pass rereads the delivered ids above the
        watermark and skips them by `_ahead`; both cost stay bounded by the gap
        horizon and the write rate. The database caps a transaction's life
        below the horizon (`db.base`, `idle_in_transaction_session_timeout`),
        so a gap is short in practice.
        """
        while True:
            while (self._last_id + 1) in self._ahead:
                self._ahead.pop(self._last_id + 1)
                self._last_id += 1
            if not self._ahead:
                return
            low = min(self._ahead)
            #: The lowest delivered id above the gap: if what it carries is
            #: older than the horizon, every transaction that could still fill
            #: the gap has ended, so the missing ids were rolled back.
            if self._ahead[low] > now - GAP_HORIZON:
                return
            self._ahead.pop(low)
            self._last_id = low

    async def _settle(self, db: AsyncSession) -> None:
        """Skip the backlog: a listener that comes later starts from now. The
        watermark **adopts** the journal's end and nothing above it is pending.

        Adopts, not `max()`: the mark belongs to the journal the hub reads,
        and that journal can begin lower than the hub remembers -- a restore
        from a backup, another database (which is what a test does when the
        process serves one database after another). Keeping the higher mark
        would mean delivering nothing until the new journal grew past it.
        """
        self._last_id = (await db.execute(select(func.max(Event.id)))).scalar() or 0
        self._ahead.clear()

    async def pump(self) -> None:
        """Deliver everything the journal holds after the watermark, and every
        touch that arrived. Safe to call at any time: it is what the listener
        does when woken, and what a test does instead of waiting."""
        touches, self._touches = self._touches, []
        for note in touches:
            await self._deliver_touch(note)
        now = _now()
        if not any(sink.listening for sink in self.sinks):
            #: Nobody to tell: just move the watermark, so the next listener
            #: does not get the backlog of an empty room.
            async with session_factory()() as db:
                await self._settle(db)
            return
        async with session_factory()() as db:
            while True:
                rows = (
                    (
                        await db.execute(
                            select(Event)
                            .where(Event.id > self._last_id)
                            .order_by(Event.id)
                            .limit(PUMP_BATCH)
                        )
                    )
                    .scalars()
                    .all()
                )
                for row in rows:
                    if row.id in self._ahead:
                        continue
                    await self._deliver(db, row, self.sinks)
                    self._ahead[row.id] = row.at
                self._advance(now)
                if len(rows) < PUMP_BATCH:
                    break

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
                await self._deliver(db, row, {sink}, indexed=False)

    async def _deliver(
        self, db: AsyncSession, row: Event, sinks: set[Sink], *, indexed: bool = True
    ) -> None:
        """Tell the sinks concerned. `indexed`: the candidates come from the
        identity/node indexes (the live pump); a replay names its one sink."""
        kind = row.kind
        prefix = kind.split(".", 1)[0]
        own = touches_of(kind)
        public = prefix in NODE_VISIBLE_PREFIXES or kind in NODE_VISIBLE_KINDS
        named = prefix in NAMED_PREFIXES or kind in NAMED_KINDS
        parties = _parties(row)
        city = _city_of(row)
        citizens: set[uuid.UUID] = set()
        if indexed:
            self.reindex()
        if city is not None and (self.by_identity if indexed else sinks):
            found = await town.by_id(db, city)
            if found is not None:
                citizens = {c.identity_id for c in await town.citizens_of(db, found)}
        who: str | None = None

        #: Only the sinks that can be concerned: parties, citizens, the room.
        if indexed:
            candidates: set[Sink] = set()
            for identity in parties | citizens:
                candidates |= self.by_identity.get(identity, set())
            if public and row.node_id is not None:
                candidates |= self.by_node.get(row.node_id, set())
        else:
            candidates = set(sinks)

        for sink in sorted(candidates, key=id):
            if not sink.listening or sink.identity_id is None:
                continue
            personal = sink.identity_id in parties
            #: A citizen hears the city's affairs as their own: the touches of
            #: the kind, not the bystander's `node`.
            member = not personal and sink.identity_id in citizens
            bystander = (
                not personal
                and not member
                and public
                and row.node_id is not None
                and sink.node_id == row.node_id
            )
            if not personal and not member and not bystander:
                continue
            message: dict[str, Any] = {
                "event": kind,
                "seq": row.id,
                "at": row.at.isoformat(),
                "touches": list(own if personal or member else NODE_TOUCHES.get(prefix, ("node",))),
            }
            others = member or (personal and row.actor_identity_id != sink.identity_id)
            if others or (bystander and named):
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
                self.dirty = True
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
        plumbing = ("touches", "identity_id", "node_id", "event", "who")
        for key, value in note.items():
            if key not in plumbing and value is not None:
                message[key] = value
        self.reindex()
        if identity is not None:
            concerned = self.by_identity.get(identity, set())
        elif node is not None:
            concerned = self.by_node.get(node, set())
        else:
            concerned = set()
        for sink in list(concerned):
            await sink.send(message)


def _parties(row: Event) -> set[uuid.UUID]:
    parties: set[uuid.UUID] = set()
    if row.actor_identity_id is not None:
        parties.add(row.actor_identity_id)
    payload = row.payload or {}
    for key, value in payload.items():
        if key in ADDRESSEE_KEYS or key.endswith(ADDRESSEE_SUFFIX):
            found = _uuid(value)
            if found is not None:
                parties.add(found)
    return parties


def _city_of(row: Event) -> uuid.UUID | None:
    """The city whose citizens hear this event, if it is a city's affair."""
    prefix = row.kind.split(".", 1)[0]
    if prefix not in CITY_VISIBLE_PREFIXES and row.kind not in CITY_VISIBLE_KINDS:
        return None
    return _uuid((row.payload or {}).get("city_id"))


def _follow(sink: Sink, row: Event) -> None:
    """The sink follows the body: arrival, printing, a find that moved the
    scout, a sentence that moved the convict. In transit and in death the
    body is nowhere."""
    if row.kind in ("travel.arrived", "travel.cancelled", "body.printed", "explore.found"):
        sink.node_id = row.node_id
    elif row.kind in ("travel.started", "body.died"):
        sink.node_id = None
    elif row.kind == "justice.sanction_applied":
        cell = _uuid((row.payload or {}).get("cell_node_id"))
        if cell is not None:
            sink.node_id = cell


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


@teller("market.order_cancelled")
async def _market_order_cancelled(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    message.pop("who", None)
    return _carry(row, message, "order_id")


@teller("market.order_expired")
async def _market_order_expired(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    message.pop("who", None)
    return _carry(row, message, "order_id")


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
