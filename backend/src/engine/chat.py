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
              * chat.leak_location_modifier[place type]
              * chat.leak_quiet_multiplier   -- if in an undertone

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
from src.engine import travel
from src.models.chat import ChatGroup, ChatMember, ChatMessage, Utterance
from src.models.identity import Body, BodyState, Identity
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Node
from src.runtime import CHAT_BUFFER, CHAT_TEXT_LIMIT


class ChatError(Exception):
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
    return chance * await _place_modifier(constants, session, node)


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
        raise ChatError("мёртвые не разговаривают")
    await travel.require_here(session, body)

    cleaned = text.strip()
    if not cleaned:
        raise ChatError("сказать нечего")
    if len(cleaned) > CHAT_TEXT_LIMIT:
        raise ChatError(f"реплика длиннее {CHAT_TEXT_LIMIT} знаков")

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
            from src.engine import luck

            leaked = await luck.hit(
                session, body.identity_id, luck.CHAT_LEAK, chance, dice=noise
            )

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
    return message


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
        await session.execute(
            select(ChatGroup).where(ChatGroup.node_id == body.node_id)
        )
    ).scalars().all()
    out: list[Circle] = []
    for group in rows:
        names = (
            await session.execute(
                select(Identity.name)
                .join(ChatMember, ChatMember.identity_id == Identity.id)
                .where(ChatMember.group_id == group.id)
                .order_by(Identity.name)
            )
        ).scalars().all()
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


async def gather(
    session: AsyncSession, body: Body, *, name: str | None = None
) -> ChatGroup:
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
        raise NotInRoom("этот кружок не здесь")
    await leave_groups(session, body.identity_id)
    session.add(ChatMember(group_id=group_id, identity_id=body.identity_id))
    await session.flush()


async def leave_groups(session: AsyncSession, identity_id: uuid.UUID) -> None:
    """Leave a circle. Also called by walking away: left -- left the conversation."""
    await session.execute(delete(ChatMember).where(ChatMember.identity_id == identity_id))
    await session.flush()


async def prune(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Sweep the delivery buffer. There is no history -- only the room's short memory."""
    moment = now or datetime.now(UTC)
    result = await session.execute(
        delete(ChatMessage).where(ChatMessage.at < moment - CHAT_BUFFER)
    )
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
    """How many living bodies stand in the location. Those passing by are not in the room."""
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Body)
            .where(Body.node_id == node.id, Body.state == BodyState.ALIVE)
        )
        or 0
    )


async def _place_modifier(
    constants: Constants, session: AsyncSession, node: Node
) -> float:
    """Place type: a noisy forge muffles, a quiet library gives away.

    The vault table is keyed by words ("forge", "library") -- the place is
    recognised by what stands in it, not by a separate type field.
    """

    table = constants[R.CHAT_LEAK_LOCATION_MODIFIER]
    if node.properties.get("library") and "библиотека" in table:
        return table["библиотека"]

    where = (
        await session.execute(
            select(Container).where(
                Container.kind == ContainerKind.NODE, Container.owner_id == node.id
            )
        )
    ).scalar_one_or_none()
    if where is not None:
        stations = (
            await session.execute(
                select(Item.type_key).where(Item.container_id == where.id).distinct()
            )
        ).scalars().all()
        for station in stations:
            modifier = table.get(station.lower())
            if modifier is not None:
                return modifier
    return 1.0


