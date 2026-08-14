"""Чат локации: разговор в комнате (D-043, D-050).

Слышат находящиеся рядом, вышел — вышел из разговора, истории нет. Внутри
локации — кружки: группы видны, их содержание — нет, но реплика из кружка
с небольшой вероятностью долетает до остальных.

## Откуда взялась формула утечки

Вероятность зависит **только от обстановки** — никаких характеристик персонажа
(D-058). Все слагаемые названы вольтом, движку осталось их сложить:

    шанс% = (chat.leak_base
             + chat.leak_per_person × (людей в локации − chat.leak_crowd_free)
             + chat.leak_group_size × (размер кружка − chat.leak_group_free))
            × chat.leak_location_modifier[тип места]
            × chat.leak_quiet_multiplier   — если вполголоса

Долетевшая реплика — одна фраза без контекста, с указанием кружка-источника:
ровно то, из чего растут домыслы и слухи.

## Чего здесь нет и не будет

* **Истории.** Сервер не хранит сказанное (D-070): таблица сообщений — буфер
  доставки, подметаемый тиком. Сказанное переживает разговор только записью,
  а запись — предмет и городская постройка (D-081), их ещё нет;
* **Модерации движком.** Живое общение вне юрисдикции городов, мут — санкция
  городского канала, который приезжает с городами (Э3).
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
from src.units import PERCENT


class ChatError(Exception):
    pass


class NotInRoom(ChatError):
    """Разговор в комнате требует быть в комнате."""


@dataclass(frozen=True, slots=True)
class Line:
    """Реплика, как её слышит читающий."""

    id: str
    who: str
    kind: Utterance
    quiet: bool
    text: str
    #: Услышано краем уха из чужого кружка: без контекста, одной фразой.
    overheard: bool
    #: Имя кружка-источника, если долетело из кружка.
    source: str | None
    at: datetime


@dataclass(frozen=True, slots=True)
class Circle:
    """Кружок, как его видят снаружи: состав виден, содержание — нет."""

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
    """Вероятность, что реплика из кружка долетит до остальных, в процентах."""
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
    """Сказать. Присутственное: разговор в комнате требует быть в комнате."""
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
        #: Кружок остался в другой локации — человек из него вышел ногами.
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
            leaked = noise.uniform(0, PERCENT) < chance

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
    """Что слышно отсюда: общий разговор, свой кружок и долетевшие обрывки.

    Слышно только с момента прихода в локацию: вышел из мастерской — вышел из
    разговора, и вернувшись, продолжения не услышишь.
    """
    await travel.require_here(session, body)
    moment = now or datetime.now(UTC)
    #: Горизонт — поле тела, а не вывод из истории переходов: у напечатанного
    #: или перенесённого правкой мира тела истории нет, а горизонт обязан быть.
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
                #: Общий разговор локации слышат все, кто в ней.
                ChatMessage.group_id.is_(None),
                #: Свой кружок.
                ChatMessage.group_id == my_group
                if my_group is not None
                else ChatMessage.group_id.is_(None),
                #: Чужой кружок — только то, что долетело.
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
    """Кружки локации: видно, кто с кем шепчется, но не о чём (D-043)."""
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
            #: Опустевший кружок расходится сам.
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
    """Собрать кружок. Вход свободный: подошедшего видно всем."""
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
    """Выйти из кружка. Зовётся и уходом ногами: вышел — вышел из разговора."""
    await session.execute(delete(ChatMember).where(ChatMember.identity_id == identity_id))
    await session.flush()


async def prune(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Подмести буфер доставки. Истории нет — есть недолгая память комнаты."""
    moment = now or datetime.now(UTC)
    result = await session.execute(
        delete(ChatMessage).where(ChatMessage.at < moment - CHAT_BUFFER)
    )
    return result.rowcount or 0


# --- внутреннее -------------------------------------------------------------


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
    """Сколько живых тел стоит в локации. Идущие мимо не в комнате."""
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
    """Тип места: шумная кузница глушит, тихая библиотека выдаёт.

    Таблица вольта ключуется словами («кузница», «библиотека») — место узнаётся
    по тому, что в нём стоит, а не по отдельному полю типа.
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


