# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The Net: correspondence and channels over any distance (D-044, D-069, D-222).

The room (`engine/chat.py`) is a conversation: those nearby hear, nothing is
kept. The Net is **correspondence**: a letter is kept, read later, and it takes
the road to arrive. Two things are decided here and nowhere else.

## The delay

    delay = seconds of the road between the two * comm.delay_per_second

The road is the fastest path on foot between the writer's body and the
reader's -- the same edges, the same seconds as walking them (`travel.route`).
Between planets the road is the passage: `ship.base_hours` for this hour's sky,
so a letter to Pyroxis takes longer when Pyroxis stands across the star. No
road at all on one planet -- islands, a hull in flight -- counts as the sea,
`ship.hop_hours`. A body is where the distance is measured from, so an identity
without one **reads but does not write**; a letter *to* somebody without a
body arrives at once -- the Net holds them everywhere, and there is nowhere to
measure to.

A letter's delay is measured once, on sending, and written into it: the
reader sees the letter when `delivered_at` has come. A post's delay is
measured on **reading**, from the node the author stood in to the reader's
node now: one post, many readers, each on their own road.

## Why it is cheap

The path is Dijkstra over the whole graph, and the graph is read from the
database. Neither is done per letter:

* the edge table is read into memory once and trusted for `runtime.NET_GRAPH_TTL`;
  a laid road shows in the delays within that, which is all a delay needs;
* the distance map **from one source node** is computed once and kept for
  `runtime.NET_REACH_CACHE` sources: the writer's next letter from the same
  place, and every reader of one post, look the answer up.

Between planets nothing is walked at all: orbits are arithmetic.
"""

from __future__ import annotations

import heapq
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import events, ship, travel
from src.engine.errors import Refusal
from src.engine.jobs import enqueue, handler
from src.models.city import City, Power
from src.models.identity import Body, BodyState, Identity
from src.models.job import Job, JobKind
from src.models.net import (
    NetChannel,
    NetMessage,
    NetParty,
    NetPost,
    NetSubscription,
    NetThread,
)
from src.models.world import Edge, Node, Planet
from src.runtime import (
    NET_ABOUT_LIMIT,
    NET_GRAPH_TTL,
    NET_NAME_LIMIT,
    NET_PAGE,
    NET_REACH_CACHE,
    NET_SEARCH_LIMIT,
    NET_TEXT_LIMIT,
)
from src.units import SECONDS_PER_HOUR


class NetError(Refusal):
    pass


class NoBody(NetError):
    """Writing needs a body: the processor speaks from a head (D-222)."""


class NotAllowed(NetError):
    """Not this channel's author."""


# --- the road ----------------------------------------------------------------

Adjacency = dict[uuid.UUID, list[tuple[uuid.UUID, float]]]


@dataclass(slots=True)
class Graph:
    loaded_at: datetime
    edges: Adjacency
    planets: dict[uuid.UUID, Planet]


_graph: Graph | None = None
#: Distance maps by source node, valid for the graph above. Oldest out first.
_reach: OrderedDict[uuid.UUID, dict[uuid.UUID, float]] = OrderedDict()


def forget_graph() -> None:
    """Drop the map in memory: an edge appeared or went. The TTL does the same
    for another process."""
    global _graph
    _graph = None
    _reach.clear()


async def _load(session: AsyncSession, constants: Constants, now: datetime) -> Graph:
    global _graph
    if _graph is not None and now - _graph.loaded_at < NET_GRAPH_TTL:
        return _graph
    edges: Adjacency = {}
    for edge in (await session.execute(select(Edge))).scalars():
        seconds = travel.edge_seconds(constants, edge)
        edges.setdefault(edge.node_a_id, []).append((edge.node_b_id, seconds))
        edges.setdefault(edge.node_b_id, []).append((edge.node_a_id, seconds))
    planets = {
        node_id: planet
        for node_id, planet in (await session.execute(select(Node.id, Node.planet))).all()
    }
    _graph = Graph(loaded_at=now, edges=edges, planets=planets)
    _reach.clear()
    return _graph


def _from(graph: Graph, source: uuid.UUID) -> dict[uuid.UUID, float]:
    """Seconds from one node to every node it reaches. Dijkstra, once per source."""
    known = _reach.get(source)
    if known is not None:
        _reach.move_to_end(source)
        return known
    best: dict[uuid.UUID, float] = {source: 0.0}
    queue: list[tuple[float, bytes]] = [(0.0, source.bytes)]
    while queue:
        cost, raw = heapq.heappop(queue)
        here = uuid.UUID(bytes=raw)
        if cost > best[here]:
            continue
        for neighbour, seconds in graph.edges.get(here, ()):
            step = cost + seconds
            if step < best.get(neighbour, float("inf")):
                best[neighbour] = step
                heapq.heappush(queue, (step, neighbour.bytes))
    _reach[source] = best
    while len(_reach) > NET_REACH_CACHE:
        _reach.popitem(last=False)
    return best


async def road_seconds(
    session: AsyncSession,
    constants: Constants,
    here: uuid.UUID,
    there: uuid.UUID,
    *,
    now: datetime,
) -> float:
    """How long the road between two nodes takes, seconds (D-222)."""

    if here == there:
        return 0.0
    graph = await _load(session, constants, now)
    sea = float(constants[R.SHIP_HOP_HOURS]) * SECONDS_PER_HOUR
    planet_a = graph.planets.get(here)
    planet_b = graph.planets.get(there)
    if planet_a is None or planet_b is None:
        return sea
    if planet_a is not planet_b:
        hours = await ship.base_hours(session, constants, planet_a, planet_b, at=now)
        if hours is None:
            #: No passage between these two in the vault at all: the far end
            #: of the longest one there is. Not knowing must not come out cheap.
            hours = max(float(h) for h in constants[R.SHIP_ROUTE_APART_HOURS].values())
        return float(hours) * SECONDS_PER_HOUR
    seconds = _from(graph, here).get(there)
    return sea if seconds is None else seconds


async def delay_between(
    session: AsyncSession,
    constants: Constants,
    here: uuid.UUID | None,
    there: uuid.UUID | None,
    *,
    now: datetime,
) -> timedelta:
    """The delay of a letter from one node to another. No node on either end
    -- nobody to measure to -- is no delay."""
    if here is None or there is None:
        return timedelta(0)
    seconds = await road_seconds(session, constants, here, there, now=now)
    return timedelta(seconds=seconds * float(constants[R.COMM_DELAY_PER_SECOND]))


async def _where(session: AsyncSession, identity_id: uuid.UUID) -> uuid.UUID | None:
    """The node of the identity's living body, if it has one."""
    return await session.scalar(
        select(Body.node_id).where(Body.identity_id == identity_id, Body.state == BodyState.ALIVE)
    )


async def _stand(session: AsyncSession, identity_id: uuid.UUID) -> uuid.UUID:
    node_id = await _where(session, identity_id)
    if node_id is None:
        raise NoBody("без тела в Сети только читают: писать нечем")
    return node_id


def _clean(text: str, limit: int, *, what: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        raise NetError(f"{what} пуст")
    if len(cleaned) > limit:
        raise NetError(f"{what} длиннее {limit} знаков")
    return cleaned


# --- correspondence ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ThreadView:
    id: str
    #: The other party.
    who: str
    surname: str
    last_at: datetime | None
    #: The last letter the reader can already see.
    preview: str | None
    unread: int


@dataclass(frozen=True, slots=True)
class Letter:
    id: str
    who: str
    mine: bool
    text: str
    sent_at: datetime
    delivered_at: datetime


def _pair_key(one: uuid.UUID, other: uuid.UUID) -> str:
    return ":".join(sorted((str(one), str(other))))


async def open_thread(session: AsyncSession, me: Identity, other: Identity) -> NetThread:
    """The correspondence with somebody: found, or started empty. Deciding to
    write is already a thread (D-222)."""
    if me.id == other.id:
        raise NetError("письмо себе — это дневник, а не Сеть")
    key = _pair_key(me.id, other.id)
    thread = (
        await session.execute(select(NetThread).where(NetThread.pair_key == key))
    ).scalar_one_or_none()
    if thread is None:
        thread = NetThread(pair_key=key)
        session.add(thread)
        await session.flush()
        session.add_all(
            [
                NetParty(thread_id=thread.id, identity_id=me.id),
                NetParty(thread_id=thread.id, identity_id=other.id),
            ]
        )
        await session.flush()
    return thread


async def _party(session: AsyncSession, thread_id: uuid.UUID, identity_id: uuid.UUID) -> NetParty:
    party = (
        await session.execute(
            select(NetParty).where(
                NetParty.thread_id == thread_id, NetParty.identity_id == identity_id
            )
        )
    ).scalar_one_or_none()
    if party is None:
        raise NetError("это не ваша переписка")
    return party


async def write(
    session: AsyncSession,
    constants: Constants,
    me: Identity,
    thread_id: uuid.UUID,
    text: str,
    *,
    now: datetime | None = None,
) -> NetMessage:
    """Send a letter. It leaves at once and arrives by the road (D-222)."""
    moment = now or datetime.now(UTC)
    cleaned = _clean(text, NET_TEXT_LIMIT, what="письмо")
    await _party(session, thread_id, me.id)
    here = await _stand(session, me.id)

    #: The reader's road: the other party's body. Several readers would each
    #: need a delay of their own; with two there is one.
    others = (
        (
            await session.execute(
                select(NetParty.identity_id).where(
                    NetParty.thread_id == thread_id, NetParty.identity_id != me.id
                )
            )
        )
        .scalars()
        .all()
    )
    delay = timedelta(0)
    for reader in others:
        there = await _where(session, reader)
        delay = max(delay, await delay_between(session, constants, here, there, now=moment))

    letter = NetMessage(
        thread_id=thread_id,
        identity_id=me.id,
        text=cleaned,
        sent_at=moment,
        delivered_at=moment + delay,
    )
    session.add(letter)
    thread = await session.get(NetThread, thread_id)
    thread.last_at = moment
    await session.flush()
    for reader in others:
        await _deliver(session, reader, letter.delivered_at, now=moment, event="net.letter")
    return letter


async def _deliver(
    session: AsyncSession,
    reader: uuid.UUID,
    arrives: datetime,
    *,
    now: datetime,
    event: str,
) -> None:
    """Tell the reader when it reaches them (D-226): at once if the road is
    nothing, otherwise by a job at the moment of arrival. The journal keeps
    no letters (D-222), so this is an announcement, not an event."""
    if arrives <= now:
        await events.announce(session, touches=("net",), identity_id=reader, event=event)
        return
    await enqueue(
        session,
        JobKind.NET_DELIVER,
        arrives,
        payload={"identity": str(reader), "event": event},
    )


@handler(JobKind.NET_DELIVER)
async def delivered(session: AsyncSession, job: Job) -> None:
    """The road is walked: the reader hears that something has reached them."""
    await events.announce(
        session,
        touches=("net",),
        identity_id=uuid.UUID(job.payload["identity"]),
        event=str(job.payload.get("event") or "net.letter"),
    )


def _visible(identity_id: uuid.UUID, now: datetime):
    """A letter the reader can see: their own at once, others' when delivered."""
    return or_(NetMessage.identity_id == identity_id, NetMessage.delivered_at <= now)


async def threads(
    session: AsyncSession, me_id: uuid.UUID, *, now: datetime | None = None
) -> list[ThreadView]:
    """The reader's correspondence, latest first, with what is waiting in each."""
    moment = now or datetime.now(UTC)
    mine = select(NetParty.thread_id).where(NetParty.identity_id == me_id).subquery()
    rows = (
        await session.execute(
            select(NetThread, NetParty, Identity)
            .join(NetParty, NetParty.thread_id == NetThread.id)
            .join(Identity, Identity.id == NetParty.identity_id)
            .where(NetThread.id.in_(select(mine)), NetParty.identity_id != me_id)
            .order_by(NetThread.last_at.desc().nulls_last(), NetThread.created_at.desc())
        )
    ).all()
    out: list[ThreadView] = []
    for thread, _, other in rows:
        me = await _party(session, thread.id, me_id)
        last = (
            await session.execute(
                select(NetMessage)
                .where(NetMessage.thread_id == thread.id, _visible(me_id, moment))
                .order_by(NetMessage.delivered_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        out.append(
            ThreadView(
                id=str(thread.id),
                who=other.name,
                surname=other.surname,
                last_at=thread.last_at,
                preview=None if last is None else last.text,
                unread=await _unread_in(session, thread.id, me_id, me.read_at, moment),
            )
        )
    return out


async def _unread_in(
    session: AsyncSession,
    thread_id: uuid.UUID,
    me_id: uuid.UUID,
    read_at: datetime | None,
    now: datetime,
) -> int:
    stmt = (
        select(func.count())
        .select_from(NetMessage)
        .where(
            NetMessage.thread_id == thread_id,
            NetMessage.identity_id != me_id,
            NetMessage.delivered_at <= now,
        )
    )
    if read_at is not None:
        stmt = stmt.where(NetMessage.delivered_at > read_at)
    return int(await session.scalar(stmt) or 0)


async def read_thread(
    session: AsyncSession,
    me_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> list[Letter]:
    """The thread as the reader sees it now. Reading marks it read."""
    moment = now or datetime.now(UTC)
    party = await _party(session, thread_id, me_id)
    rows = (
        await session.execute(
            select(NetMessage, Identity.name)
            .join(Identity, Identity.id == NetMessage.identity_id)
            .where(NetMessage.thread_id == thread_id, _visible(me_id, moment))
            .order_by(NetMessage.delivered_at.desc())
            .limit(NET_PAGE)
        )
    ).all()
    party.read_at = moment
    await session.flush()
    return [
        Letter(
            id=str(letter.id),
            who=who,
            mine=letter.identity_id == me_id,
            text=letter.text,
            sent_at=letter.sent_at,
            delivered_at=letter.delivered_at,
        )
        for letter, who in reversed(rows)
    ]


async def unread_letters(
    session: AsyncSession, me_id: uuid.UUID, *, now: datetime | None = None
) -> int:
    """Delivered and unread letters across all threads: the tab's count."""
    moment = now or datetime.now(UTC)
    stmt = (
        select(func.count())
        .select_from(NetMessage)
        .join(
            NetParty,
            and_(NetParty.thread_id == NetMessage.thread_id, NetParty.identity_id == me_id),
        )
        .where(
            NetMessage.identity_id != me_id,
            NetMessage.delivered_at <= moment,
            or_(NetParty.read_at.is_(None), NetMessage.delivered_at > NetParty.read_at),
        )
    )
    return int(await session.scalar(stmt) or 0)


async def find_people(
    session: AsyncSession, query: str, *, exclude: uuid.UUID
) -> list[tuple[str, str]]:
    """Names starting with what was typed: whom to write to. Names are public (D-058)."""
    typed = query.strip()
    if not typed:
        return []
    rows = (
        await session.execute(
            select(Identity.name, Identity.surname)
            .where(Identity.name.ilike(f"{typed}%"), Identity.id != exclude)
            .order_by(Identity.name)
            .limit(NET_SEARCH_LIMIT)
        )
    ).all()
    return [(name, surname) for name, surname in rows]


# --- channels ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelView:
    id: str
    name: str
    about: str
    #: The city's channel: marked as official.
    official: bool
    #: The reader writes here: their own channel, or the city's with the power.
    writable: bool
    #: Implied by citizenship -- cannot be dropped.
    implied: bool
    #: The author's name for a player's channel, the city's for the official one.
    by: str
    last_at: datetime | None
    unread: int


@dataclass(frozen=True, slots=True)
class Post:
    id: str
    who: str
    text: str
    at: datetime
    #: When it reached this reader.
    delivered_at: datetime


async def city_channel(session: AsyncSession, city: City) -> NetChannel:
    """The city's official channel: exists from the first time it is asked for."""
    channel = (
        await session.execute(select(NetChannel).where(NetChannel.city_id == city.id))
    ).scalar_one_or_none()
    if channel is None:
        channel = NetChannel(name=city.name, city_id=city.id)
        session.add(channel)
        await session.flush()
    return channel


async def create_channel(
    session: AsyncSession, me: Identity, name: str, about: str = ""
) -> NetChannel:
    cleaned = _clean(name, NET_NAME_LIMIT, what="название")
    note = about.strip()
    if len(note) > NET_ABOUT_LIMIT:
        raise NetError(f"описание длиннее {NET_ABOUT_LIMIT} знаков")
    taken = await session.scalar(
        select(NetChannel.id).where(func.lower(NetChannel.name) == cleaned.lower())
    )
    if taken is not None:
        raise NetError(f"канал «{cleaned}» уже есть")
    channel = NetChannel(name=cleaned, about=note, owner_identity_id=me.id)
    session.add(channel)
    await session.flush()
    return channel


async def _channel(session: AsyncSession, channel_id: uuid.UUID) -> NetChannel:
    channel = await session.get(NetChannel, channel_id)
    if channel is None:
        raise NetError("нет такого канала")
    return channel


async def _native_city(session: AsyncSession, identity_id: uuid.UUID) -> City | None:

    citizenship = await town.citizenship(session, identity_id)
    if citizenship is None:
        return None
    return await town.by_id(session, citizenship.city_id)


async def _subscription(
    session: AsyncSession, channel_id: uuid.UUID, identity_id: uuid.UUID
) -> NetSubscription | None:
    return (
        await session.execute(
            select(NetSubscription).where(
                NetSubscription.channel_id == channel_id,
                NetSubscription.identity_id == identity_id,
            )
        )
    ).scalar_one_or_none()


async def subscribe(session: AsyncSession, me: Identity, channel_id: uuid.UUID) -> None:
    await _channel(session, channel_id)
    row = await _subscription(session, channel_id, me.id)
    if row is None:
        session.add(NetSubscription(channel_id=channel_id, identity_id=me.id))
    else:
        row.chosen = True
    await session.flush()


async def unsubscribe(session: AsyncSession, me: Identity, channel_id: uuid.UUID) -> None:
    channel = await _channel(session, channel_id)
    if channel.owner_identity_id == me.id:
        raise NetError("от своего канала не отписываются")
    native = await _native_city(session, me.id)
    if native is not None and channel.city_id == native.id:
        raise NetError("канал своего города читают всегда: это гражданство, а не подписка")
    row = await _subscription(session, channel_id, me.id)
    if row is not None:
        await session.delete(row)
        await session.flush()


async def may_post(session: AsyncSession, me_id: uuid.UUID, channel: NetChannel) -> bool:

    if channel.owner_identity_id == me_id:
        return True
    if channel.city_id is None:
        return False
    city = await town.by_id(session, channel.city_id)
    return city is not None and await town.may(session, me_id, city, Power.CHANNEL)


async def post(
    session: AsyncSession,
    me: Identity,
    channel_id: uuid.UUID,
    text: str,
    *,
    now: datetime | None = None,
    constants: Constants | None = None,
) -> NetPost:
    """Write in a channel: the owner, or the city's power (D-222).

    With `constants` every reader is told when the post reaches them by
    their own road (D-226) -- the readers of the moment, where they stand
    now; one who walks meanwhile reads it when `channel.read` says so."""
    moment = now or datetime.now(UTC)
    channel = await _channel(session, channel_id)
    cleaned = _clean(text, NET_TEXT_LIMIT, what="пост")
    if not await may_post(session, me.id, channel):
        raise NotAllowed(
            "в этот канал пишет его автор"
            if channel.city_id is None
            else "в канал города пишут с правом «channel»"
        )
    here = await _stand(session, me.id)
    entry = NetPost(channel_id=channel.id, identity_id=me.id, node_id=here, text=cleaned, at=moment)
    session.add(entry)
    channel.last_at = moment
    await session.flush()
    if constants is not None:
        for reader in await _readers(session, channel):
            if reader == me.id:
                continue
            there = await _where(session, reader)
            delay = await delay_between(session, constants, here, there, now=moment)
            await _deliver(session, reader, moment + delay, now=moment, event="net.post")
    return entry


async def _readers(session: AsyncSession, channel: NetChannel) -> set[uuid.UUID]:
    """Who reads the channel now: subscribers, and the citizens of its city."""
    readers = set(
        (
            await session.execute(
                select(NetSubscription.identity_id).where(NetSubscription.channel_id == channel.id)
            )
        )
        .scalars()
        .all()
    )
    if channel.city_id is not None:
        city = await town.by_id(session, channel.city_id)
        if city is not None:
            readers.update(c.identity_id for c in await town.citizens_of(session, city))
    return readers


async def _delivered(
    session: AsyncSession,
    constants: Constants,
    rows: list[tuple[NetPost, str]],
    reader_node: uuid.UUID | None,
    now: datetime,
) -> list[Post]:
    """Which of the posts have reached this reader, each by its own road."""
    delays: dict[uuid.UUID, timedelta] = {}
    out: list[Post] = []
    for entry, who in rows:
        if entry.node_id not in delays:
            delays[entry.node_id] = await delay_between(
                session, constants, entry.node_id, reader_node, now=now
            )
        arrives = entry.at + delays[entry.node_id]
        if arrives <= now:
            out.append(
                Post(id=str(entry.id), who=who, text=entry.text, at=entry.at, delivered_at=arrives)
            )
    return out


async def _posts_since(
    session: AsyncSession, channel_id: uuid.UUID, since: datetime | None, *, limit: int
) -> list[tuple[NetPost, str]]:
    stmt = (
        select(NetPost, Identity.name)
        .join(Identity, Identity.id == NetPost.identity_id)
        .where(NetPost.channel_id == channel_id)
        .order_by(NetPost.at.desc())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(NetPost.at > since)
    return list((await session.execute(stmt)).all())


async def channels(
    session: AsyncSession,
    constants: Constants,
    me_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> list[ChannelView]:
    """The reader's channels: own, chosen, and the city's by citizenship."""

    moment = now or datetime.now(UTC)
    native = await _native_city(session, me_id)
    seen: dict[uuid.UUID, NetChannel] = {}
    if native is not None:
        official = await city_channel(session, native)
        seen[official.id] = official
    for channel in (
        await session.execute(
            select(NetChannel)
            .join(
                NetSubscription,
                and_(
                    NetSubscription.channel_id == NetChannel.id,
                    NetSubscription.identity_id == me_id,
                    NetSubscription.chosen.is_(True),
                ),
                isouter=True,
            )
            .where(or_(NetChannel.owner_identity_id == me_id, NetSubscription.id.is_not(None)))
        )
    ).scalars():
        seen.setdefault(channel.id, channel)

    reader_node = await _where(session, me_id)
    out: list[ChannelView] = []
    for channel in seen.values():
        row = await _subscription(session, channel.id, me_id)
        read_at = None if row is None else row.read_at
        fresh = await _posts_since(session, channel.id, read_at, limit=NET_PAGE)
        arrived = await _delivered(session, constants, fresh, reader_node, moment)
        if channel.city_id is not None:
            city = await town.by_id(session, channel.city_id)
            by = city.name if city is not None else channel.name
        else:
            by = (
                await session.scalar(
                    select(Identity.name).where(Identity.id == channel.owner_identity_id)
                )
                or "?"
            )
        out.append(
            ChannelView(
                id=str(channel.id),
                name=channel.name,
                about=channel.about,
                official=channel.city_id is not None,
                writable=await may_post(session, me_id, channel),
                implied=native is not None and channel.city_id == native.id,
                by=by,
                last_at=channel.last_at,
                unread=len(arrived),
            )
        )
    #: Official first, then by the latest post.
    dawn = datetime.min.replace(tzinfo=UTC)
    out.sort(key=lambda view: (not view.official, -(view.last_at or dawn).timestamp()))
    return out


async def read_channel(
    session: AsyncSession,
    constants: Constants,
    me_id: uuid.UUID,
    channel_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> tuple[NetChannel, list[Post]]:
    """The channel as this reader sees it now. Reading marks it read."""
    moment = now or datetime.now(UTC)
    channel = await _channel(session, channel_id)
    reader_node = await _where(session, me_id)
    rows = await _posts_since(session, channel.id, None, limit=NET_PAGE)
    arrived = await _delivered(session, constants, rows, reader_node, moment)
    row = await _subscription(session, channel_id, me_id)
    if row is None:
        #: A reader of an implied or foreign channel gets a row for `read_at`
        #: only: it is not a choice, and the list does not show it as one.
        row = NetSubscription(channel_id=channel_id, identity_id=me_id, chosen=False)
        session.add(row)
    row.read_at = moment
    await session.flush()
    return channel, list(reversed(arrived))


async def find_channels(
    session: AsyncSession, query: str, *, me_id: uuid.UUID
) -> list[tuple[NetChannel, str]]:
    """Channels by name, with who writes them: what there is to subscribe to."""

    typed = query.strip()
    stmt = select(NetChannel).order_by(NetChannel.last_at.desc().nulls_last()).limit(NET_PAGE)
    if typed:
        stmt = stmt.where(NetChannel.name.ilike(f"%{typed}%"))
    out: list[tuple[NetChannel, str]] = []
    for channel in (await session.execute(stmt)).scalars():
        if channel.city_id is not None:
            city = await town.by_id(session, channel.city_id)
            by = city.name if city is not None else channel.name
        else:
            by = (
                await session.scalar(
                    select(Identity.name).where(Identity.id == channel.owner_identity_id)
                )
                or "?"
            )
        out.append((channel, by))
    return out


async def unread_posts(
    session: AsyncSession,
    constants: Constants,
    me_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> int:
    """Delivered and unread posts across the reader's channels: the tab's count."""
    return sum(view.unread for view in await channels(session, constants, me_id, now=now))
