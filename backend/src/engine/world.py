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

from src.constants import current
from src.constants import registry as R
from src.engine import events
from src.models.event import EventKind
from src.models.identity import Account, Body, BodyState, Identity, Knowledge, KnowledgeKind
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Layer, Node, Planet, Vein
from src.units import amount as to_amount


async def create_node(
    session: AsyncSession,
    key: str,
    name: str,
    *,
    planet: Planet = Planet.TERRA,
    area_m2: float,
    properties: dict[str, Any] | None = None,
    layer: Layer = Layer.CITY,
    parent: Node | None = None,
) -> Node:
    node = Node(
        key=key,
        name=name,
        planet=planet,
        layer=layer,
        parent_id=None if parent is None else parent.id,
        area_m2=Decimal(str(area_m2)),
        properties=properties or {},
    )
    session.add(node)
    await session.flush()
    return node


class LandError(Exception):
    pass


async def claim_node(session: AsyncSession, body: Body, node: Node) -> Node:
    """Занять участок: присутственно, в диком узле (06-farming).

    Городскую землю не занимают — её покупают или арендуют у города (Э3).
    Хозяйство на участке ведёт только владелец: наём — это доступ плюс доля
    через договор (D-116), а не общая земля.
    """
    from src.engine import travel

    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise LandError("участок занимают ногами: дойдите до него")
    if node.owner_city_id is not None:
        raise LandError("это городская земля: её покупают или арендуют, а не занимают")
    if node.owner_identity_id is not None:
        raise LandError("участок уже занят")

    node.owner_identity_id = body.identity_id
    await session.flush()

    #: Владение оформляется бумагой: электронный документ, который дальше
    #: продаётся договором купли-продажи (D-116).
    from src.engine import estate

    await estate.issue_deed(session, node, body.identity_id)

    await events.record(
        session,
        EventKind.LAND_CLAIMED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
    )
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


#: Станок, у которого печатают тела (D-033). Пока биопринтера нигде не стоит,
#: печатают у ядра города — узла нулевого кольца (D-089).
BIOPRINTER = "Биопринтер"


async def spawn_point(session: AsyncSession) -> Node | None:
    """Где печатается новое тело: биопринтер, иначе ядро города.

    Ищется по миру, а не по ключу из сида: мир вправе состоять из других узлов,
    а печататься людям где-то надо.
    """
    from src.models.inventory import Container, ContainerKind

    печатает = (
        await session.execute(
            select(Node)
            .join(Container, Container.owner_id == Node.id)
            .join(Item, Item.container_id == Container.id)
            .where(Container.kind == ContainerKind.NODE, Item.type_key == BIOPRINTER)
            .limit(1)
        )
    ).scalars().first()
    if печатает is not None:
        return печатает

    узлы = (
        await session.execute(select(Node).where(Node.layer == Layer.CITY))
    ).scalars().all()
    ядро = [узел for узел in узлы if узел.properties.get("кольцо") == 0]
    if ядро:
        return ядро[0]
    return узлы[0] if узлы else None


async def spawn(session: AsyncSession, name: str, node: Node) -> tuple[Identity, Body]:
    """Новый игрок: личность, тело у биопринтера и **ноль на счету** (D-153).

    Мир денег не выдаёт: любой такой выпуск был бы эмиссией, размывающей деньги
    всех остальных. Зато город вправе заплатить подъёмные из своей казны — это
    перевод, а не эмиссия, и решает его власть, а не движок.

    Почему город на это идёт: новый житель — это ВВП. Он покупает, продаёт и
    платит налоги, значит подъёмные окупаются. Богатый город переманивает
    новичков, бедный не может себе этого позволить.
    """
    from src.constants import current_catalog
    from src.engine import city as town

    существует = (
        await session.execute(select(Identity).where(Identity.name == name))
    ).scalar_one_or_none()
    if существует is not None:
        raise ValueError(f"имя {name!r} уже занято: имя сменить нельзя (D-011)")

    identity = await create_identity(session, name)
    body = await print_body(session, identity, node)

    город = await town.of_node(session, node)
    if город is not None:
        await town.welcome(session, current(), current_catalog(), город, identity)
    return identity, body


async def body_container(session: AsyncSession, body: Body) -> Container:
    stmt = select(Container).where(
        Container.kind == ContainerKind.BODY, Container.owner_id == body.id
    )
    container = (await session.execute(stmt)).scalar_one_or_none()
    if container is None:  # pragma: no cover — тело без инвентаря это баг
        raise RuntimeError(f"у тела {body.id} нет инвентаря")
    return container


async def node_container(session: AsyncSession, node: Node) -> Container:
    """Что стоит и лежит в узле: станки, изделия у станка.

    До зданий (Э3) это единственное место, где может стоять станок. Со
    зданиями оно переедет в них — станок задаёт, чем здание является (D-106).
    """
    stmt = select(Container).where(
        Container.kind == ContainerKind.NODE, Container.owner_id == node.id
    )
    container = (await session.execute(stmt)).scalar_one_or_none()
    if container is None:
        container = Container(kind=ContainerKind.NODE, owner_id=node.id)
        session.add(container)
        await session.flush()
    return container


#: Станок «Библиотека» (D-176): окно библиотеки показывается там, где он стоит.
LIBRARY = "Библиотека"


async def has_station(session: AsyncSession, node: Node, name: str) -> bool:
    """Стоит ли в узле станок с этим именем: сцена узла собирается из станков
    (D-176), и это единственный способ спросить, чем место является."""
    двор = await node_container(session, node)
    found = await session.scalar(
        select(Item.id)
        .where(Item.container_id == двор.id, Item.type_key == name)
        .limit(1)
    )
    return found is not None


async def is_library(session: AsyncSession, node: Node) -> bool:
    """Библиотека — станок, а не свойство узла (D-176). Свойство `library`
    остаётся наследием старых миров: догоняющий сид ставит станок, но мир,
    который догнать не успели, не должен потерять окно."""
    if (node.properties or {}).get("library"):
        return True
    return await has_station(session, node, LIBRARY)


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
