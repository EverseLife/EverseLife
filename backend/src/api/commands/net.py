# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Room talk and the Net.

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.common import _alive, _identity, _stamp
from src.api.commands.views import _identity_by_name
from src.api.registry import command
from src.constants import current
from src.engine import (
    chat,
    net,
)
from src.models.chat import Utterance


@command("chat.say")
async def _chat_say(state: dict, db: AsyncSession, message: dict) -> dict:
    """Say something in the location. The kind is required: speech, action or out-of-game
    (D-050)."""
    body = await _alive(state, db)
    said = await chat.say(
        db,
        current(),
        body,
        str(message.get("text", "")),
        kind=Utterance(message.get("kind", Utterance.SPEECH.value)),
        quiet=bool(message.get("quiet", False)),
    )
    return {"said": str(said.id), "leaked": said.leaked}


@command("chat.hear")
async def _chat_hear(state: dict, db: AsyncSession, message: dict) -> dict:
    """What is heard and who whispers with whom. Room talk -- only from the room."""
    body = await _alive(state, db)
    return {
        "lines": [
            {
                "id": line.id,
                "who": line.who,
                "kind": line.kind.value,
                "quiet": line.quiet,
                "text": line.text,
                "overheard": line.overheard,
                "source": line.source,
                "at": line.at.isoformat(),
            }
            for line in await chat.hear(db, body)
        ],
        "circles": [
            {
                "id": circle.id,
                "name": circle.name,
                "members": list(circle.members),
                "mine": circle.mine,
            }
            for circle in await chat.circles(db, body)
        ],
    }


@command("chat.gather")
async def _chat_gather(state: dict, db: AsyncSession, message: dict) -> dict:
    """Gather a circle. Visible to all: "these ones are arranging something" (D-043)."""
    body = await _alive(state, db)
    group = await chat.gather(db, body, name=message.get("name") or None)
    return {"circle": str(group.id)}


@command("chat.join")
async def _chat_join(state: dict, db: AsyncSession, message: dict) -> dict:
    """Join a circle in the room: `circle` is its id from `chat.hear` (D-043). One circle at a
    time."""
    body = await _alive(state, db)
    await chat.join(db, body, uuid.UUID(message["circle"]))
    return {"joined": message["circle"]}


@command("chat.leave")
async def _chat_leave(state: dict, db: AsyncSession, message: dict) -> dict:
    """Leave the circle you are in. Walking out of the room does the same."""
    await chat.leave_groups(db, state["identity_id"])
    return {"left": True}


@command("net.threads")
async def _net_threads(state: dict, db: AsyncSession, message: dict) -> dict:
    """Correspondence and channels: the sidebar's "Net" tab in one reading."""
    me = await _identity(state, db)
    return {
        "threads": [
            {
                "id": view.id,
                "who": view.who,
                "surname": view.surname,
                "last_at": _stamp(view.last_at),
                "preview": view.preview,
                "unread": view.unread,
            }
            for view in await net.threads(db, me.id)
        ],
        "channels": [_channel_view(view) for view in await net.channels(db, current(), me.id)],
    }


def _channel_view(view: net.ChannelView) -> dict[str, Any]:
    return {
        "id": view.id,
        "name": view.name,
        "about": view.about,
        "official": view.official,
        "writable": view.writable,
        "implied": view.implied,
        "by": view.by,
        "last_at": _stamp(view.last_at),
        "unread": view.unread,
    }


@command("net.open")
async def _net_open(state: dict, db: AsyncSession, message: dict) -> dict:
    """Start (or find) the correspondence with somebody: an empty thread is kept."""
    me = await _identity(state, db)
    other = await _identity_by_name(db, str(message.get("name", "")))
    thread = await net.open_thread(db, me, other)
    return {"thread": str(thread.id), "who": other.name}


@command("net.read")
async def _net_read(state: dict, db: AsyncSession, message: dict) -> dict:
    """Read a thread of letters: `thread` is its id; a letter shows when it has arrived (D-222)."""
    me = await _identity(state, db)
    letters = await net.read_thread(db, me.id, uuid.UUID(message["thread"]))
    return {
        "letters": [
            {
                "id": letter.id,
                "who": letter.who,
                "mine": letter.mine,
                "text": letter.text,
                "sent_at": letter.sent_at.isoformat(),
                "delivered_at": letter.delivered_at.isoformat(),
            }
            for letter in letters
        ]
    }


@command("net.write")
async def _net_write(state: dict, db: AsyncSession, message: dict) -> dict:
    """Write a letter in a thread: `thread`, `text`. It leaves at once and arrives by the road
    (D-222)."""
    me = await _identity(state, db)
    letter = await net.write(
        db, current(), me, uuid.UUID(message["thread"]), str(message.get("text", ""))
    )
    return {"sent": str(letter.id), "delivered_at": letter.delivered_at.isoformat()}


@command("net.people")
async def _net_people(state: dict, db: AsyncSession, message: dict) -> dict:
    """Whom to write to: names starting with what was typed."""
    me = await _identity(state, db)
    found = await net.find_people(db, str(message.get("query", "")), exclude=me.id)
    return {"people": [{"name": name, "surname": surname} for name, surname in found]}


@command("net.channel.create")
async def _net_channel_create(state: dict, db: AsyncSession, message: dict) -> dict:
    """Open a channel of your own in the Net: `name`, optional `about` (D-222)."""
    me = await _identity(state, db)
    channel = await net.create_channel(
        db, me, str(message.get("name", "")), str(message.get("about", ""))
    )
    return {"channel": str(channel.id)}


@command("net.channel.find")
async def _net_channel_find(state: dict, db: AsyncSession, message: dict) -> dict:
    """What there is to subscribe to."""
    me = await _identity(state, db)
    mine = {view.id for view in await net.channels(db, current(), me.id)}
    found = await net.find_channels(db, str(message.get("query", "")), me_id=me.id)
    return {
        "channels": [
            {
                "id": str(channel.id),
                "name": channel.name,
                "about": channel.about,
                "official": channel.city_id is not None,
                "by": by,
                "subscribed": str(channel.id) in mine,
            }
            for channel, by in found
        ]
    }


@command("net.subscribe")
async def _net_subscribe(state: dict, db: AsyncSession, message: dict) -> dict:
    """Subscribe to a channel: `channel` is its id. A city's channel needs no subscription from its
    citizens."""
    me = await _identity(state, db)
    await net.subscribe(db, me, uuid.UUID(message["channel"]))
    return {"subscribed": message["channel"]}


@command("net.unsubscribe")
async def _net_unsubscribe(state: dict, db: AsyncSession, message: dict) -> dict:
    """Drop a channel: `channel` is its id. The city's own cannot be dropped."""
    me = await _identity(state, db)
    await net.unsubscribe(db, me, uuid.UUID(message["channel"]))
    return {"unsubscribed": message["channel"]}


@command("net.channel.read")
async def _net_channel_read(state: dict, db: AsyncSession, message: dict) -> dict:
    """Read a channel: `channel` is its id; posts arrive by the road from where they were written
    (D-222)."""
    me = await _identity(state, db)
    channel, posts = await net.read_channel(db, current(), me.id, uuid.UUID(message["channel"]))
    return {
        "channel": {
            "id": str(channel.id),
            "name": channel.name,
            "about": channel.about,
            "official": channel.city_id is not None,
            "writable": await net.may_post(db, me.id, channel),
        },
        "posts": [
            {
                "id": entry.id,
                "who": entry.who,
                "text": entry.text,
                "at": entry.at.isoformat(),
                "delivered_at": entry.delivered_at.isoformat(),
            }
            for entry in posts
        ],
    }


@command("net.post")
async def _net_post(state: dict, db: AsyncSession, message: dict) -> dict:
    """Post in a channel you may write in: `channel`, `text` (D-222)."""
    me = await _identity(state, db)
    entry = await net.post(
        db, me, uuid.UUID(message["channel"]), str(message.get("text", "")), constants=current()
    )
    return {"posted": str(entry.id)}
