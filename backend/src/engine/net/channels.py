# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Channels: a city's word and anybody's, subscription and the right to
post, and reading that pays the same road delay a letter does.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.engine import city as town
from src.engine.net._base import NetError, NotAllowed, _clean, _deliver, _stand, _where
from src.engine.net.road import delay_between
from src.models.city import City, Power
from src.models.identity import Identity
from src.models.net import (
    NetChannel,
    NetPost,
    NetSubscription,
)
from src.runtime import (
    NET_ABOUT_LIMIT,
    NET_NAME_LIMIT,
    NET_PAGE,
    NET_TEXT_LIMIT,
)

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
    cleaned = _clean(name, NET_NAME_LIMIT, what="name")
    note = about.strip()
    if len(note) > NET_ABOUT_LIMIT:
        raise NetError(key="net-about-too-long", limit=NET_ABOUT_LIMIT)
    taken = await session.scalar(
        select(NetChannel.id).where(func.lower(NetChannel.name) == cleaned.lower())
    )
    if taken is not None:
        raise NetError(key="net-channel-exists", channel=cleaned)
    channel = NetChannel(name=cleaned, about=note, owner_identity_id=me.id)
    session.add(channel)
    await session.flush()
    return channel


async def _channel(session: AsyncSession, channel_id: uuid.UUID) -> NetChannel:
    channel = await session.get(NetChannel, channel_id)
    if channel is None:
        raise NetError(key="net-no-such-channel")
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
        raise NetError(key="net-own-channel-kept")
    native = await _native_city(session, me.id)
    if native is not None and channel.city_id == native.id:
        raise NetError(key="net-city-channel-kept")
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
    cleaned = _clean(text, NET_TEXT_LIMIT, what="post")
    if not await may_post(session, me.id, channel):
        raise NotAllowed(
            key="net-cannot-post",
            channel="own" if channel.city_id is None else "city",
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
