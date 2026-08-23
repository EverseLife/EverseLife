# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: docking, flight, docking.

Split out of `engine/ship.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current
from src.constants import registry as R
from src.engine import events, travel, world
from src.engine.jobs import enqueue, handler
from src.engine.ship._base import (
    _EPS,
    FUEL,
    SPACEPORT,
    Docked,
    InFlight,
    NoFuel,
    NoLifeSupport,
    NoPort,
    NotAboard,
    NotEnoughThrust,
    NotYours,
    ShipError,
    TooFar,
    _free_berth,
    _gangway_seconds,
)
from src.engine.ship.belonging import aboard_of, crew_of, is_aboard, nodes_of
from src.engine.ship.building import _planet_root, _spend
from src.engine.ship.physics import (
    _things,
    base_hours,
    engine_class,
    fuel_aboard,
    fuel_for,
    life_support,
    mass,
    passage_hours,
    ratio,
    route_class,
)
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.job import Job, JobKind
from src.models.ship import Ship
from src.models.world import Node, Surface
from src.units import (
    ROUND_HOURS,
    ROUND_MASS,
    ROUND_RATIO,
)


async def _commanded_by(session: AsyncSession, body: Body, ship: Ship) -> None:
    """Who may move the ship: its owner, standing aboard.

    A guest aboard is carried away and cannot object -- that is deliberate
    (D-201): a ban would mean any stranger blocks a passage by standing in the
    hold. The dispute is a matter for the court (D-166), not for the engine.
    """
    if body.state is not BodyState.ALIVE:
        raise ShipError("мёртвое тело кораблём не управляет")
    await travel.require_here(session, body)
    if ship.owner_identity_id != body.identity_id:
        raise NotYours("это чужой корабль")
    aboard = await aboard_of(session, body)
    if aboard is None or aboard.id != ship.id:
        raise NotAboard("кораблём управляют с борта: поднимитесь на него")


async def undock(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    ship: Ship,
    *,
    now: datetime | None = None,
) -> Ship:
    """Cast off: the edge to the port is removed, and that is the flight (D-201).

    Four refusals, and every one of them is known before the attempt: not
    enough thrust for the mass, more people aboard than the life support holds,
    not enough fuel even for the way back, and somebody walking the gangway
    right now -- one does not pull it from under a walker.
    """
    moment = now or datetime.now(UTC)
    await _commanded_by(session, body, ship)
    if ship.docked_node_id is None:
        raise InFlight("корабль уже отстыкован")

    port = await session.get(Node, ship.docked_node_id)
    connector = await session.get(Node, ship.connector_node_id)
    if port is None or connector is None:  # pragma: no cover
        raise ShipError("у корабля нет коннектора или порта")

    thrust_ratio = await ratio(session, constants, catalog, ship)
    floor = constants[R.SHIP_MIN_THRUST_RATIO]
    if thrust_ratio < floor:
        raise NotEnoughThrust(
            f"тяги {thrust_ratio:.2f} на килограмм при нужных {floor:.2f}: "
            "корабль не отрывается. Ставьте двигатели или снимайте груз"
        )
    crew = len(await crew_of(session, ship))
    holds = await life_support(session, constants, ship)
    if crew > holds:
        raise NoLifeSupport(
            f"на борту {crew} человек, а жизнеобеспечение держит {holds}: ставьте ещё систему"
        )

    #: Fuel for at least the way back to this very port. An undocked ship has
    #: no edge to it at all, so nobody can bring fuel out to it and nobody
    #: aboard can walk off: casting off without a passage in the tanks would be
    #: a trap with no way out, and this world does not build those (pillar P6).
    #: The return hop is the cheapest passage there is, so affording it
    #: guarantees at least one way home.
    weight = await mass(session, constants, catalog, ship)
    table = await base_hours(session, constants, connector.planet, port.planet, at=moment)
    back = fuel_for(constants, weight, passage_hours(constants, table or 0, thrust_ratio))
    if await fuel_aboard(session, ship) + _EPS < back:
        raise NoFuel(
            f"на возврат в этот же порт нужно {back:.1f} «{FUEL}», а столько на "
            "борту нет: отстыкованный корабль недостижим, и топливо ему не привезут"
        )

    #: The whole undocking. `travel.disconnect` refuses if somebody is walking
    #: the edge, and that refusal travels up as it is.
    await travel.disconnect(session, port, connector)
    ship.docked_node_id = None
    #: The berth is given back with the gangway: a ship in flight holds no place
    #: at a pier, and the next arrival gets this one rather than a longer walk.
    ship.berth = None
    await session.flush()

    await events.record(
        session,
        EventKind.SHIP_UNDOCKED,
        actor_identity_id=body.identity_id,
        node_id=port.id,
        ship_id=str(ship.id),
        name=ship.name,
        port=port.key,
        crew=crew,
        ratio=round(thrust_ratio, ROUND_RATIO),
    )
    return ship


async def fly(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    ship: Ship,
    port: Node,
    *,
    now: datetime | None = None,
) -> Job:
    """Set out for a spaceport. Fuel now, arrival by a journal job.

    The route's class is decided by the **weakest** engine aboard (D-037): one
    poor engine in the cluster holds the cluster back.
    """
    moment = now or datetime.now(UTC)
    await _commanded_by(session, body, ship)
    if ship.docked_node_id is not None:
        raise Docked("корабль пристыкован: сначала отстыкуйтесь")
    if not await world.has_station(session, port, SPACEPORT):
        raise NoPort(f"в «{port.name}» нет космодрома: причалить не к чему")
    if is_aboard(port):  # pragma: no cover -- a port is never a ship node
        raise NoPort("к борту не причаливают: цель рейса — космодром")

    connector = await session.get(Node, ship.connector_node_id)
    if connector is None:  # pragma: no cover
        raise ShipError("у корабля нет коннектора")

    #: The time is settled **here**, at the moment of casting off, and is not
    #: recomputed afterwards: otherwise the sky would turn under a ship already
    #: under way and the passage would grow longer than the one paid for.
    table = await base_hours(session, constants, connector.planet, port.planet, at=moment)
    if table is None:
        raise TooFar(f"маршрута {connector.planet.value} — {port.planet.value} в мире нет")
    need_class = route_class(constants, connector.planet, port.planet)
    have_class = await engine_class(session, constants, ship)
    if have_class is None:
        raise NotEnoughThrust("на корабле нет ни одного двигателя")
    if have_class < need_class:
        raise TooFar(
            f"маршрут требует двигателя {need_class} класса, а слабейший на "
            f"борту — {have_class}: класс задаёт самое слабое звено"
        )

    thrust_ratio = await ratio(session, constants, catalog, ship)
    floor = constants[R.SHIP_MIN_THRUST_RATIO]
    if thrust_ratio < floor:
        raise NotEnoughThrust(
            f"тяги {thrust_ratio:.2f} на килограмм при нужных {floor:.2f}: "
            "с такой массой рейс не начинается"
        )

    hours = passage_hours(constants, table, thrust_ratio)
    weight = await mass(session, constants, catalog, ship)
    need_fuel = fuel_for(constants, weight, hours)
    have_fuel = await fuel_aboard(session, ship)
    if have_fuel + _EPS < need_fuel:
        raise NoFuel(f"на рейс нужно {need_fuel:.1f} «{FUEL}», а на борту {have_fuel:.1f}")
    burnt = await _spend(
        session,
        [
            thing
            for thing in await _things(session, ship)
            if thing.type_key in world.station_names(FUEL)
        ],
        need_fuel,
    )

    arrives = moment + timedelta(hours=hours)
    event = await events.record(
        session,
        EventKind.SHIP_LAUNCHED,
        actor_identity_id=body.identity_id,
        node_id=connector.id,
        ship_id=str(ship.id),
        name=ship.name,
        to=port.key,
        hours=round(hours, ROUND_HOURS),
        fuel=burnt,
        mass=round(weight, ROUND_MASS),
        ratio=round(thrust_ratio, ROUND_RATIO),
        arrives_at=arrives.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.SHIP_FLIGHT,
        arrives,
        payload={"ship": str(ship.id), "to": str(port.id)},
        dedup_key=f"ship.flight:{ship.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise ShipError("рейс уже поставлен")
    return job


@handler(JobKind.SHIP_FLIGHT)
async def arrived(session: AsyncSession, job: Job) -> None:
    """The passage is over: the edge to the port appears, and one may walk aboard again."""

    ship = await session.get(Ship, uuid.UUID(job.payload["ship"]))
    port = await session.get(Node, uuid.UUID(job.payload["to"]))
    if ship is None or port is None:  # pragma: no cover
        raise ShipError(f"рейс {job.id} ведёт в никуда")
    connector = await session.get(Node, ship.connector_node_id)
    if connector is None:  # pragma: no cover
        raise ShipError("у корабля нет коннектора")

    #: The berth is taken on arrival, and it is whichever is free **there**:
    #: a ship does not carry its place from the port it left.
    ship.berth = await _free_berth(session, port)
    await travel.connect(
        session,
        port,
        connector,
        base_seconds=_gangway_seconds(current(), ship.berth),
        surface=Surface.PAVED,
    )
    ship.docked_node_id = port.id
    await _moor_to(session, ship, port)
    await session.flush()

    await events.record(
        session,
        EventKind.SHIP_DOCKED,
        actor_identity_id=ship.owner_identity_id,
        node_id=port.id,
        ship_id=str(ship.id),
        name=ship.name,
        port=port.key,
    )


async def _moor_to(session: AsyncSession, ship: Ship, port: Node) -> None:
    """The ship's nodes take the planet of the port it now stands at.

    Nodes aboard need a planet -- day length and environment wear are counted
    from it (D-201) -- and it must be the planet the ship is **actually** at
    rather than the one it was laid down at. Otherwise a ship that flew to
    Aurora would price its way home as a local hop between two Terran ports:
    the route is chosen by the pair of planets, and one of the pair would be a
    memory of the shipyard.

    The group moves with it: the delegate node hangs under the planet it is at,
    so the map shows the ship where it is.
    """
    delegate = await session.get(Node, ship.node_id)
    root = await _planet_root(session, port)
    aboard = await nodes_of(session, ship)
    for node in [*aboard, *([delegate] if delegate is not None else [])]:
        node.planet = port.planet
    if delegate is not None and root is not None:
        delegate.parent_id = root.id
    await session.flush()
