# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Location chat: conversation in a room (D-043, D-050).

Those nearby hear; left -- left the conversation; there is no history. Inside
a location -- circles: groups are visible, their content is not, but a remark
from a circle reaches the others with a small probability.

## Where the leak formula came from

The probability depends **only on the setting** -- no character stats (D-058).
All terms are named by the vault, the engine only had to add them up:

    chance% = (chat.leak_base
               + chat.leak_per_person * (people in location - chat.leak_crowd_free)
               + chat.leak_group_size * (circle size - chat.leak_group_free))
              * crowding                     -- people * chat.leak_space_per_person
                                                / the node's area, clamped to
                                                [chat.leak_crowding_min, ...max]
              * chat.leak_quiet_multiplier   -- if in an undertone

The place itself has no voice (D-349): neither a forge nor a library changes
what is overheard. What changes it is how tightly the room is packed.

A leaked remark is one phrase without context, with the source circle named:
exactly what conjecture and rumour grow from.

## What is not here and will not be

* **History.** The server does not store what was said (D-070): the message
  table is a delivery buffer swept by the tick. What was said outlives the
  conversation only as a record, and a record is an item and a city building
  (D-081), which do not exist yet;
* **Engine moderation.** Live talk is outside city jurisdiction; a mute is a
  sanction of the city channel, which arrives with cities (E3).
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import events, luck, travel
from src.engine.errors import Refusal
from src.models.chat import ChatGroup, ChatMember, ChatMessage, Utterance
from src.models.identity import Body, BodyState, Identity
from src.models.world import Node
from src.runtime import CHAT_BUFFER, CHAT_TEXT_LIMIT


class ChatError(Refusal):
    pass


class NotInRoom(ChatError):
    """Talking in a room requires being in the room."""


@dataclass(frozen=True, slots=True)
class Line:
    """A remark as the reader hears it."""

    id: str
    who: str
    kind: Utterance
    quiet: bool
    text: str
    #: Overheard from somebody else's circle: without context, one phrase.
    overheard: bool
    #: The source circle's name, if it leaked from a circle.
    source: str | None
    at: datetime


@dataclass(frozen=True, slots=True)
class Circle:
    """A circle as seen from outside: membership visible, content not."""

    id: str
    name: str | None
    members: tuple[str, ...]
    mine: bool


async def leak_chance(
    constants: Constants,
    session: AsyncSession,
    node: Node,
    group_size: int,
) -> float:
    """The probability that a remark from a circle reaches the others, in percent."""
    in_room = await _people_in(session, node)
    crowd = max(0, in_room - int(constants[R.CHAT_LEAK_CROWD_FREE]))
    loud = max(0, group_size - int(constants[R.CHAT_LEAK_GROUP_FREE]))
    chance = (
        constants[R.CHAT_LEAK_BASE]
        + constants[R.CHAT_LEAK_PER_PERSON] * crowd
        + constants[R.CHAT_LEAK_GROUP_SIZE] * loud
    )
    return chance * _crowding(constants, node, in_room)


async def say(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    text: str,
    *,
    kind: Utterance,
    quiet: bool = False,
    rng: random.Random | None = None,
) -> ChatMessage:
    """Say. In person: talking in a room requires being in the room."""
    if body.state is not BodyState.ALIVE:
        raise ChatError(key="chat-dead-are-silent")
    await travel.require_here(session, body)

    cleaned = text.strip()
    if not cleaned:
        raise ChatError(key="chat-nothing-to-say")
    if len(cleaned) > CHAT_TEXT_LIMIT:
        raise ChatError(key="chat-too-long", limit=CHAT_TEXT_LIMIT)

    membership = await _membership(session, body.identity_id)
    group_id: uuid.UUID | None = None
    leaked = False
    if membership is not None:
        group = await session.get(ChatGroup, membership.group_id)
        #: The circle stayed in another location -- the person walked out of it.
        if group is None or group.node_id != body.node_id:
            await leave_groups(session, body.identity_id)
        else:
            group_id = group.id
            noise = rng or random.Random()
            size = await _group_size(session, group.id)
            node = await session.get(Node, body.node_id)
            chance = await leak_chance(constants, session, node, size)
            if quiet:
                chance *= constants[R.CHAT_LEAK_QUIET_MULTIPLIER]
            #: A memory of its own (D-213): a circle that leaked three times
            #: running is not a circle any more, and one that never leaks is
            #: not a secret worth keeping.

            leaked = await luck.hit(session, body.identity_id, luck.CHAT_LEAK, chance, dice=noise)

    message = ChatMessage(
        node_id=body.node_id,
        group_id=group_id,
        identity_id=body.identity_id,
        kind=kind,
        quiet=quiet,
        text=cleaned,
        leaked=leaked,
    )
    session.add(message)
    await session.flush()
    #: The line goes to whoever may hear it (D-226, wave 2): common talk to
    #: the room whole; a circle's line to its members whole, and to the room
    #: as a leaked fragment when it leaked (D-043). A member may get both --
    #: the client keeps one by `id`. The journal still keeps nothing (D-070).
    speaker = await session.get(Identity, body.identity_id)
    line = {
        "id": str(message.id),
        "who": speaker.name if speaker else "",
        "kind": kind.value,
        "quiet": quiet,
        "text": cleaned,
        "at": message.at.isoformat(),
    }
    asleep = await _asleep_in(session, body.node_id)
    if group_id is None:
        await events.announce(
            session,
            touches=("chat",),
            node_id=body.node_id,
            event="chat.said",
            line=line,
            asleep=asleep,
        )
    else:
        group = await session.get(ChatGroup, group_id)
        for member in await _members(session, group_id):
            await events.announce(
                session,
                touches=("chat",),
                identity_id=member,
                event="chat.said",
                line={**line, "source": group.name if group else None},
            )
        if leaked:
            await events.announce(
                session,
                touches=("chat",),
                node_id=body.node_id,
                event="chat.said",
                line={
                    **line,
                    "overheard": True,
                    "source": (group.name if group else None) or "кружок",
                },
                asleep=asleep,
            )
    return message


async def _members(session: AsyncSession, group_id: uuid.UUID) -> list[uuid.UUID]:
    """Members of the circle who stand in its room **and can hear**: one who
    walked out hears nothing until `leave_groups` catches up with them, and a
    sleeper hears nothing at all -- `hear` refuses them (D-091, D-211), so the
    live line must not reach them either. Asked of the delivery alone: what the
    leak costs is priced by the room (`_people_in`), and that count is a
    question of its own (OQ-180)."""
    group = await session.get(ChatGroup, group_id)
    if group is None:
        return []
    stmt = (
        select(ChatMember.identity_id)
        .join(Body, Body.identity_id == ChatMember.identity_id)
        .where(
            ChatMember.group_id == group_id,
            Body.node_id == group.node_id,
            Body.state == BodyState.ALIVE,
            Body.sleeping_since.is_(None),
        )
    )
    return list((await session.execute(stmt)).scalars().all())


async def _asleep_in(session: AsyncSession, node_id: uuid.UUID) -> list[str]:
    """Who lies asleep in the room: the talk is not delivered to them.

    The room's line goes out as one note for the node (`events.announce`), and
    the push has no session of its own to ask with -- so the names of those who
    cannot hear travel with the note and the pump drops their sinks. Plumbing,
    not state: the key never reaches a client (`api.push.pump`).
    """
    stmt = select(Body.identity_id).where(
        Body.node_id == node_id,
        Body.state == BodyState.ALIVE,
        Body.sleeping_since.is_not(None),
    )
    return [str(one) for one in (await session.execute(stmt)).scalars().all()]


async def hear(
    session: AsyncSession,
    body: Body,
    *,
    now: datetime | None = None,
) -> list[Line]:
    """What is heard from here: the common talk, own circle and leaked fragments.

    Heard only since arriving in the location: left the workshop -- left the
    conversation, and on return you will not hear the continuation.
    """
    await travel.require_here(session, body)
    moment = now or datetime.now(UTC)
    #: The horizon is a body field, not derived from transit history: a printed
    #: body or one moved by a world edit has no history, and the horizon must exist.
    horizon = max(body.node_since, moment - CHAT_BUFFER)

    membership = await _membership(session, body.identity_id)
    my_group = membership.group_id if membership is not None else None

    stmt = (
        select(ChatMessage, Identity.name, ChatGroup.name)
        .join(Identity, Identity.id == ChatMessage.identity_id)
        .join(ChatGroup, ChatGroup.id == ChatMessage.group_id, isouter=True)
        .where(
            ChatMessage.node_id == body.node_id,
            ChatMessage.at >= horizon,
            or_(
                #: The location's common talk is heard by everyone in it.
                ChatMessage.group_id.is_(None),
                #: Own circle.
                ChatMessage.group_id == my_group
                if my_group is not None
                else ChatMessage.group_id.is_(None),
                #: Somebody else's circle -- only what leaked.
                ChatMessage.leaked.is_(True),
            ),
        )
        .order_by(ChatMessage.at)
    )
    lines: list[Line] = []
    for message, who, group_name in (await session.execute(stmt)).all():
        foreign = message.group_id is not None and message.group_id != my_group
        lines.append(
            Line(
                id=str(message.id),
                who=who,
                kind=message.kind,
                quiet=message.quiet,
                text=message.text,
                overheard=foreign,
                source=(group_name or "кружок") if foreign else group_name,
                at=message.at,
            )
        )
    return lines


async def circles(session: AsyncSession, body: Body) -> list[Circle]:
    """The location's circles: visible who whispers with whom, but not about what (D-043)."""
    membership = await _membership(session, body.identity_id)
    my_group = membership.group_id if membership is not None else None

    rows = (
        (await session.execute(select(ChatGroup).where(ChatGroup.node_id == body.node_id)))
        .scalars()
        .all()
    )
    out: list[Circle] = []
    for group in rows:
        names = (
            (
                await session.execute(
                    select(Identity.name)
                    .join(ChatMember, ChatMember.identity_id == Identity.id)
                    .where(ChatMember.group_id == group.id)
                    .order_by(Identity.name)
                )
            )
            .scalars()
            .all()
        )
        if not names:
            #: An emptied circle disbands by itself.
            await session.delete(group)
            continue
        out.append(
            Circle(
                id=str(group.id),
                name=group.name,
                members=tuple(names),
                mine=group.id == my_group,
            )
        )
    await session.flush()
    return out


async def gather(session: AsyncSession, body: Body, *, name: str | None = None) -> ChatGroup:
    """Gather a circle. Entry is free: whoever comes up is seen by all."""
    await travel.require_here(session, body)
    group = ChatGroup(node_id=body.node_id, name=name)
    session.add(group)
    await session.flush()
    await join(session, body, group.id)
    return group


async def join(session: AsyncSession, body: Body, group_id: uuid.UUID) -> None:
    await travel.require_here(session, body)
    group = await session.get(ChatGroup, group_id)
    if group is None or group.node_id != body.node_id:
        raise NotInRoom(key="chat-group-not-here")
    await leave_groups(session, body.identity_id)
    session.add(ChatMember(group_id=group_id, identity_id=body.identity_id))
    await session.flush()
    #: Circles are visible to the room (D-043): who stands with whom changed.
    await events.announce(session, touches=("chat",), node_id=body.node_id, event="chat.circled")


async def leave_groups(session: AsyncSession, identity_id: uuid.UUID) -> None:
    """Leave a circle. Also called by walking away: left -- left the conversation."""
    await session.execute(delete(ChatMember).where(ChatMember.identity_id == identity_id))
    await session.flush()


async def prune(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Sweep the delivery buffer. There is no history -- only the room's short memory."""
    moment = now or datetime.now(UTC)
    result = await session.execute(delete(ChatMessage).where(ChatMessage.at < moment - CHAT_BUFFER))
    return result.rowcount or 0


# --- internal ----------------------------------------------------------------


async def _membership(session: AsyncSession, identity_id: uuid.UUID) -> ChatMember | None:
    stmt = select(ChatMember).where(ChatMember.identity_id == identity_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _group_size(session: AsyncSession, group_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(ChatMember).where(ChatMember.group_id == group_id)
        )
        or 0
    )


async def _people_in(session: AsyncSession, node: Node) -> int:
    """How many living bodies stand in the location. Those passing by are not in the room.

    One who has set out still has this node in `node_id` until the arrival
    and is not counted: a traveller stands in no node (D-290 p. 1), and the
    room the talk head names is the room the leak is priced by.

    **A sleeper is counted** (D-349): they lie in this room, and how crowded a
    room is is how crowded it is. It also closes a trick -- putting the
    neighbours to sleep would otherwise empty the room and make the talk safe.
    Not to be confused with delivery, which skips a sleeper: the count measures
    the room, not the listeners. So, for now, is a scout on a run: a run has no
    transit row yet, and the engine keeps them home (the gap D-327 names, not
    a rule).
    """
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Body)
            .where(
                Body.node_id == node.id,
                Body.state == BodyState.ALIVE,
                ~travel.on_the_road(Body.id),
            )
        )
        or 0
    )


def _crowding(constants: Constants, node: Node, in_room: int) -> float:
    """How tightly the room is packed, as a multiplier (D-349).

    What stands in a place says nothing about what is overheard in it: the
    forge and the library lost their rows with this decision. What says it is
    the floor per head -- `chat.leak_space_per_person` is the room one person
    needs to be out of earshot -- and the multiplier is how far this node
    falls short of it.

    Clamped at both ends by the vault. The floor is there because a rumour in
    an empty workshop is rare and not impossible; the ceiling, because heads
    are counted twice -- once in the sum, once here -- and unclamped the pair
    would grow faster than the crowd itself.
    """
    low = constants[R.CHAT_LEAK_CROWDING_MIN]
    high = constants[R.CHAT_LEAK_CROWDING_MAX]
    area = float(node.area_m2 or 0)
    #: No area is no room at all, and the limit of the formula there is the
    #: ceiling. Every node has one, so this is a guard and not a branch of the
    #: rule: dividing by it must not be what tells us the column went empty.
    if area <= 0:  # pragma: no cover -- a node without an area is a bug
        return high
    return min(high, max(low, in_room * constants[R.CHAT_LEAK_SPACE_PER_PERSON] / area))
