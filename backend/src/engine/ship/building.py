# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: building.

Split out of `engine/ship.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current
from src.constants import registry as R
from src.engine import events, travel, world
from src.engine.jobs import enqueue, handler
from src.engine.ship._base import (
    _EPS,
    ABOARD,
    FOUNDATION,
    SPACEPORT,
    NoFoundation,
    NoPort,
    NotAboard,
    NotYours,
    ShipError,
    _free_berth,
    _gangway_seconds,
)
from src.engine.ship.belonging import is_aboard, nodes_of, of_node
from src.models.estate import Building
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.job import Job, JobKind
from src.models.ship import Ship
from src.models.world import Layer, Node, Planet, Surface
from src.units import amount as to_amount
from src.units import (
    amount_float,
)


async def _foundation_at_hand(session: AsyncSession, body: Body) -> list[Item]:
    pocket = await world.body_container(session, body)
    return list(
        (
            await session.execute(
                select(Item).where(
                    Item.container_id == pocket.id,
                    Item.type_key.in_(world.station_names(FOUNDATION)),
                )
            )
        )
        .scalars()
        .all()
    )


async def _spend(session: AsyncSession, stacks: list[Item], quantity: float) -> float:
    """Write off this much from these stacks. Returns what could be taken.

    The stacks are locked first: the foundation in a pocket and the fuel in
    the rooms are shared with whoever carries them at the same moment."""
    locked = await world.lock_items(session, stacks)
    return amount_float(await world.consume(session, locked, to_amount(quantity)))


async def found(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    name: str,
    *,
    now: datetime | None = None,
) -> Job:
    """Lay a ship's foundation at a spaceport. The node arrives on schedule.

    The foundation is written off up front, like batch materials: work that
    lacked material does not start at all. What appears at the deadline is the
    base -- the connector and the docking point in one node.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise ShipError("мёртвое тело кораблей не закладывает")
    await travel.require_here(session, body)

    title = name.strip()
    if not title:
        raise ShipError("у корабля должно быть имя")

    port = await session.get(Node, body.node_id)
    if port is None:  # pragma: no cover -- a body always stands in a node
        raise ShipError("тело вне узла")
    if not await world.has_station(session, port, SPACEPORT):
        raise NoPort("основание корабля закладывают на космодроме: причалить больше некуда")
    #: Not onto another ship, even one carrying a spaceport aboard: that would
    #: be a second ship welded to the first for good, and ship-to-ship docking
    #: is a question of design, not a side effect (D-201). A ship is grown from
    #: the inside -- `extend`.
    if is_aboard(port):
        raise NoPort(
            "к борту новый корабль не закладывают: основание кладут на "
            "космодроме планеты, а борт расширяют изнутри"
        )
    return await _lay(session, constants, body, port, ship=None, name=title, now=moment)


async def extend(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    now: datetime | None = None,
) -> Job:
    """Lay one more node aboard, joined to the one it is laid from.

    The same action and the same item as the foundation (D-202): the ship grows
    by a node at a time, and its shape is the owner's decision.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise ShipError("мёртвое тело кораблей не строит")
    await travel.require_here(session, body)

    here = await session.get(Node, body.node_id)
    if here is None:  # pragma: no cover
        raise ShipError("тело вне узла")
    ship = await of_node(session, here)
    if ship is None:
        raise NotAboard(
            "корабль расширяют с борта: встаньте в узел корабля. "
            "Первый узел закладывают на космодроме"
        )
    if ship.owner_identity_id != body.identity_id:
        raise NotYours("это чужой корабль: строят у себя")
    return await _lay(session, constants, body, here, ship=ship, name=None, now=moment)


async def _lay(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    at: Node,
    *,
    ship: Ship | None,
    name: str | None,
    now: datetime,
) -> Job:
    """Common part of the two layings: check, write off, queue."""
    stacks = await _foundation_at_hand(session, body)
    in_hands = sum(amount_float(stack.amount) for stack in stacks)
    if in_hands + _EPS < 1:
        raise NoFoundation(
            f"нужна «{FOUNDATION}», а её в руках нет: корабль — это материалы, а не намерение"
        )
    await _spend(session, stacks, 1)

    ready_ = now + timedelta(hours=constants[R.SHIP_FOUNDATION_HOURS])
    event = await events.record(
        session,
        EventKind.SHIP_KEEL_LAID,
        actor_identity_id=body.identity_id,
        node_id=at.id,
        ship_id=None if ship is None else str(ship.id),
        name=name,
        ready_at=ready_.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.SHIP_KEEL,
        ready_,
        payload={
            "at": str(at.id),
            "ship": None if ship is None else str(ship.id),
            "name": name,
            "owner": str(body.identity_id),
        },
        dedup_key=f"ship.keel:{at.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise ShipError("закладка уже поставлена")
    return job


@handler(JobKind.SHIP_KEEL)
async def keel_laid(session: AsyncSession, job: Job) -> None:
    """The node aboard is ready: it appears together with its edge.

    A node without an edge would be a piece of map nobody can reach, so the two
    are one action: the base couples to the port, every other node to the one
    it was laid from.
    """

    constants = current()
    at = await session.get(Node, uuid.UUID(job.payload["at"]))
    if at is None:  # pragma: no cover -- nodes do not vanish
        raise ShipError(f"закладка {job.id}: узла нет")
    owner = uuid.UUID(job.payload["owner"])

    raw_ship = job.payload.get("ship")
    ship = None if raw_ship is None else await session.get(Ship, uuid.UUID(raw_ship))
    if raw_ship is not None and ship is None:  # pragma: no cover
        raise ShipError(f"закладка {job.id}: корабля нет")

    if ship is None:
        ship, node = await _found_ship(
            session, constants, at, owner=owner, name=str(job.payload.get("name") or "Корабль")
        )
        kind, joined = EventKind.SHIP_FOUNDED, at.key
    else:
        node = await _add_node(session, constants, ship, at, owner=owner)
        kind, joined = EventKind.SHIP_EXTENDED, at.key

    await events.record(
        session,
        kind,
        actor_identity_id=owner,
        node_id=node.id,
        ship_id=str(ship.id),
        name=ship.name,
        node=node.key,
        joined_to=joined,
        nodes=len(await nodes_of(session, ship)),
    )


async def _found_ship(
    session: AsyncSession,
    constants: Constants,
    port: Node,
    *,
    owner: uuid.UUID,
    name: str,
) -> tuple[Ship, Node]:
    """A new ship: the delegate node, the connector and the edge to the port."""
    delegate = await world.create_node(
        session,
        f"ship.{uuid.uuid4().hex}",
        name,
        planet=port.planet,
        area_m2=constants[R.SHIP_NODE_AREA],
        layer=Layer.SPACE,
        parent=await _planet_root(session, port),
        properties={ABOARD: True},
    )
    connector = await _node_aboard(
        session, constants, delegate, "Основание", owner=owner, planet=port.planet
    )

    ship = Ship(
        name=name,
        owner_identity_id=owner,
        node_id=delegate.id,
        connector_node_id=connector.id,
        docked_node_id=port.id,
        berth=await _free_berth(session, port),
    )
    session.add(ship)
    await session.flush()

    #: The gangway: as long as the berth's number, and a road like any other --
    #: paved, because a pier is not a trail.
    await travel.connect(
        session,
        port,
        connector,
        base_seconds=_gangway_seconds(constants, ship.berth),
        surface=Surface.PAVED,
    )
    return ship, connector


async def _add_node(
    session: AsyncSession, constants: Constants, ship: Ship, at: Node, *, owner: uuid.UUID
) -> Node:
    """One more node aboard, joined to the one it was laid from."""
    delegate = await session.get(Node, ship.node_id)
    if delegate is None:  # pragma: no cover
        raise ShipError("у корабля нет группы")
    node = await _node_aboard(session, constants, delegate, "Отсек", owner=owner, planet=at.planet)
    #: A step between adjacent rooms is the shortest there is: inside a ship one
    #: walks as inside a city, and `travel.city_step` is that very step (D-045).
    await travel.connect(
        session,
        at,
        node,
        base_seconds=constants[R.TRAVEL_CITY_STEP].min,
        surface=Surface.PAVED,
    )
    return node


async def _node_aboard(
    session: AsyncSession,
    constants: Constants,
    delegate: Node,
    name: str,
    *,
    owner: uuid.UUID,
    planet: Planet,
) -> Node:
    """A node aboard: a room with an area, an owner and a building in it.

    **A building from the first second**, because machines are placed into a
    building and take its area (D-106): without it an engine would have nowhere
    to stand. And **an owner**, because a ship belongs to a person: nobody's
    node is open to all (D-198), and a stranger would carry the engine away.
    """
    node = await world.create_node(
        session,
        f"ship.node.{uuid.uuid4().hex}",
        name,
        planet=planet,
        area_m2=constants[R.SHIP_NODE_AREA],
        layer=Layer.LOCATION,
        parent=delegate,
        properties={ABOARD: True},
    )
    node.owner_identity_id = owner
    from src.engine import estate  # noqa: PLC0415 -- lazy: breaks the import cycle with estate

    session.add(
        Building(
            node_id=node.id,
            area_m2=Decimal(str(constants[R.SHIP_NODE_AREA])),
            footprint_m2=Decimal(str(constants[R.SHIP_NODE_AREA])),
            floors=1,
            #: A hull is registered as a building only so that area and places
            #: are counted by one rule (D-106, D-202). Of the earthly types the
            #: dearest is the nearest -- a ship is metal and glass -- and decay
            #: passes it by: what keeps a ship up is not the weather over a yard.
            kind=estate.kinds(constants)[-1],
        )
    )
    await session.flush()
    return node


async def _planet_root(session: AsyncSession, node: Node) -> Node | None:
    """The planet the node stands on -- the ship hangs on it as a group."""
    current_node = node
    while current_node.parent_id is not None:
        parent = await session.get(Node, current_node.parent_id)
        if parent is None:  # pragma: no cover
            return None
        if parent.layer is Layer.SPACE:
            return parent
        current_node = parent
    return None
