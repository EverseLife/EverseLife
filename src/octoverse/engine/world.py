"""Создание того, что существует в мире: узлы, личности, тела, имущество.

Ни одна функция здесь не создаёт материю из ничего просто так: предметы
появляются только через добычу и урожай (инвариант И1). `grant_item` — это
инструмент сеанса разработки и сценариев, и он пишет событие с явным
основанием, чтобы такой приход было видно в телеметрии.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from octoverse.constants import current
from octoverse.constants import registry as R
from octoverse.engine import events
from octoverse.models.event import EventKind
from octoverse.models.identity import Account, Body, BodyState, Identity, Knowledge, KnowledgeKind
from octoverse.models.inventory import Container, ContainerKind, Item
from octoverse.models.world import Node, Planet, Vein
from octoverse.units import amount as to_amount


async def create_node(
    session: AsyncSession,
    key: str,
    name: str,
    *,
    planet: Planet = Planet.TERRA,
    area_m2: float,
    properties: dict[str, Any] | None = None,
) -> Node:
    node = Node(
        key=key,
        name=name,
        planet=planet,
        area_m2=Decimal(str(area_m2)),
        properties=properties or {},
    )
    session.add(node)
    await session.flush()
    return node


async def create_vein(
    session: AsyncSession,
    node: Node,
    resource: str,
    *,
    richness: float,
    remaining: float,
) -> Vein:
    vein = Vein(
        node_id=node.id,
        resource=resource,
        richness=Decimal(str(richness)),
        remaining=to_amount(remaining),
    )
    session.add(vein)
    await session.flush()
    return vein


async def create_identity(session: AsyncSession, name: str) -> Identity:
    """Аккаунт и личность. Один аккаунт — одна личность (D-011)."""
    account = Account()
    session.add(account)
    await session.flush()

    identity = Identity(account_id=account.id, name=name)
    session.add(identity)
    await session.flush()

    await events.record(
        session, EventKind.IDENTITY_CREATED, actor_identity_id=identity.id, name=name
    )
    return identity


async def print_body(session: AsyncSession, identity: Identity, node: Node) -> Body:
    """Напечатать тело. Личность при этом не меняется — она вечна (D-012)."""
    stamina = current()[R.BODY_STAMINA_MAX]
    body = Body(
        identity_id=identity.id,
        node_id=node.id,
        state=BodyState.ALIVE,
        stamina=Decimal(str(stamina)),
    )
    session.add(body)
    await session.flush()

    session.add(Container(kind=ContainerKind.BODY, owner_id=body.id))
    await session.flush()

    await events.record(
        session,
        EventKind.BODY_PRINTED,
        actor_identity_id=identity.id,
        node_id=node.id,
        body_id=str(body.id),
    )
    return body


async def body_container(session: AsyncSession, body: Body) -> Container:
    stmt = select(Container).where(
        Container.kind == ContainerKind.BODY, Container.owner_id == body.id
    )
    container = (await session.execute(stmt)).scalar_one_or_none()
    if container is None:  # pragma: no cover — тело без инвентаря это баг
        raise RuntimeError(f"у тела {body.id} нет инвентаря")
    return container


async def learn(
    session: AsyncSession,
    identity: Identity,
    key: str,
    *,
    kind: KnowledgeKind = KnowledgeKind.RECIPE,
    discovered: bool = False,
) -> Knowledge | None:
    """Скопировать знание в личность. Бесплатно и навсегда (D-053)."""
    stmt = select(Knowledge).where(
        Knowledge.identity_id == identity.id, Knowledge.kind == kind, Knowledge.key == key
    )
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        return None

    knowledge = Knowledge(
        identity_id=identity.id, kind=kind, key=key, discovered=discovered
    )
    session.add(knowledge)
    await session.flush()
    await events.record(
        session,
        EventKind.KNOWLEDGE_LEARNED,
        actor_identity_id=identity.id,
        kind_of_knowledge=kind.value,
        key=key,
    )
    return knowledge


async def grant_item(
    session: AsyncSession,
    container: Container,
    type_key: str,
    *,
    amount: float = 1,
    quality: float | None = None,
    origin: str,
    maker_identity_id: uuid.UUID | None = None,
    made_node_id: uuid.UUID | None = None,
) -> Item:
    """Положить предмет в контейнер.

    `origin` обязателен и попадает в событие: любое появление материи в мире
    должно иметь названное основание — добыча, урожай, крафт, сценарий отладки.
    Безымянного прихода не бывает (столп П1).
    """
    item = Item(
        container_id=container.id,
        type_key=type_key,
        amount=to_amount(amount),
        quality=None if quality is None else Decimal(str(quality)),
        maker_identity_id=maker_identity_id,
        made_at=datetime.now(UTC) if maker_identity_id else None,
        made_node_id=made_node_id,
    )
    session.add(item)
    await session.flush()
    await events.record(
        session,
        EventKind.ITEM_CREATED,
        actor_identity_id=maker_identity_id,
        item_id=str(item.id),
        type_key=type_key,
        amount=amount,
        quality=quality,
        origin=origin,
    )
    return item
