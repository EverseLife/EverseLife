# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: docking, flight, docking.

Split out of `engine/ship.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current
from src.constants import registry as R
from src.engine import events, travel, world
from src.engine.jobs import enqueue, handler
from src.engine.ship._base import (
    _EPS,
    BRIDGE,
    FUEL,
    GROUND_BRIDGE,
    SPACEPORT,
    Deaf,
    Docked,
    InFlight,
    NoConsole,
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
    base_hours,
    engine_class,
    fuel_aboard,
    fuel_for,
    fuel_stacks,
    life_support,
    mass,
    passage_hours,
    ratio,
)
from src.engine.ship.view import beacon_lit, lands_anywhere, open_landings
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.job import Job, JobKind, JobState
from src.models.ship import Ship
from src.models.world import Node, Surface
from src.units import (
    ROUND_HOURS,
    ROUND_MASS,
    ROUND_RATIO,
    SECONDS_PER_HOUR,
)


async def _passage_of(session: AsyncSession, ship: Ship, *, lock: bool = False) -> Job | None:
    """The passage this ship is on, if it is on one.

    A passage lives in its journal job alone: it was queued at the casting off
    and fires on arrival, so an unfinished one **is** the ship being under way.

    `RUNNING` is matched for the day the journal starts using it: today a
    claimed job keeps `PENDING` and is held by `locked_by` (`jobs._claim`), so
    the state never appears. It is listed rather than left out because the one
    thing that must not happen here is an under-way hull reading as free --
    and unlike a wedged hull, that one cannot be undone by waiting.
    """
    stmt = select(Job).where(
        Job.kind == JobKind.SHIP_FLIGHT.value,
        Job.state.in_((JobState.PENDING, JobState.RUNNING)),
        Job.payload["ship"].astext == str(ship.id),
    )
    if lock:
        #: **Before** the hull's own row, never after. The journal claims a job
        #: first and writes the ship second (`jobs._claim`, `arrived`), so an
        #: order that took the ship first and reached for the job second would
        #: be the other half of a deadlock -- and a player would get a database
        #: error where a refusal belongs.
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalars().first()


async def _has_bridge(session: AsyncSession, ship: Ship) -> bool:
    """Whether the hull carries a console of its own -- something to receive an order.

    Asked of every room: a bridge is a machine standing somewhere aboard, and
    which compartment it is in is the owner's business.
    """
    for room in await nodes_of(session, ship):
        if await world.has_station(session, room, BRIDGE):
            return True
    return False


async def _commanded_by(session: AsyncSession, body: Body, ship: Ship) -> None:
    """Who may move the ship: its owner, at a console -- aboard, or on the ground.

    Two places, and the second is why the first is not enough (D-242). Standing
    at the bridge aboard is the ordinary way (D-230). But a crew that dies in
    flight leaves a hull with no edges: unreachable on foot, deaf to every
    order, hanging with its cargo for ever -- and this world does not build
    traps with no way out (pillar P6). So the owner may also stand at a
    **«Наземная консоль управления»** in a building of their own and give the
    same orders: an order is information, and information travels the Net while
    matter requires presence (D-044).

    What the ground console does **not** do is make a bridge optional: the hull
    must carry one to have anything to receive the order with. A ship built
    without a console does not fly at all, by its crew or by anybody.

    A guest aboard is carried away and cannot object -- that is deliberate
    (D-201): a ban would mean any stranger blocks a passage by standing in the
    hold. The dispute is a matter for the court (D-166), not for the engine.
    Who gets to the console at all is the owner's door (`engine.access`): a
    room aboard is theirs, and they list who may enter it.
    """
    if body.state is not BodyState.ALIVE:
        raise ShipError("мёртвое тело кораблём не управляет")
    await travel.require_here(session, body)
    if ship.owner_identity_id != body.identity_id:
        raise NotYours("это чужой корабль")

    here = await session.get(Node, body.node_id)
    if here is None:  # pragma: no cover -- a body always stands in a node
        raise ShipError("тело вне узла")

    aboard = await aboard_of(session, body)
    if aboard is not None and aboard.id == ship.id:
        if not await world.has_station(session, here, BRIDGE):
            raise NoConsole(
                "кораблём управляют от консоли: встаньте в отсек, где стоит "
                "«Консоль управления кораблём»"
            )
        return

    #: Not aboard this hull. Then it is the ground console or nothing -- and the
    #: hull must have something to hear it with.
    if not await world.has_station(session, here, GROUND_BRIDGE):
        raise NotAboard(
            "кораблём управляют с борта или от «Наземной консоли управления»: "
            "поднимитесь на него либо встаньте к наземной консоли"
        )
    #: **Your own** console, on land you dispose of (D-242). Not a security
    #: measure -- the ship is refused to a stranger by its owner above -- but
    #: the difference between a private pult and a public one: a single console
    #: in the capital would otherwise fly every fleet in the world.
    #: Lazy: `station` reaches `estate`, and `estate` reaches back here.
    from src.engine import station  # noqa: PLC0415 -- lazy: breaks the cycle with estate

    if not await station.may_build(session, body, here):
        raise NotYours(
            "консоль чужая: приказы отдают со своей. Поставьте «Наземную консоль "
            "управления» в своём здании"
        )
    if not await _has_bridge(session, ship):
        raise Deaf(
            f"на «{ship.name}» нет рубки: приказ с земли принимать нечем. "
            "Поставьте на борт «Консоль управления кораблём»"
        )


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
    #: Held while it is decided, as in `fly`: this and that are the two writes
    #: into the hull's row, and two casting-offs given together would both pass
    #: an unlocked check. Nothing worse than a doubled event comes of it today,
    #: but the pair of orders is one pair and is guarded as one.
    await session.refresh(ship, with_for_update=True)
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
    #: Where it came from, remembered (D-242). Undocking is the moment that
    #: erases the other end of every later passage, and "turn back" has to point
    #: somewhere: the job under way knows only where it is going.
    ship.left_node_id = port.id
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
    #: One hull, one passage -- and the hull is held while that is decided.
    #: Undocking leaves the ship with no edge at all, and "not docked" was the
    #: only thing asked here, so a second order given while the first was still
    #: under way was taken: the fuel was burnt twice and two arrivals stood in
    #: the journal, each ready to set the hull down in its own port. Two orders
    #: given in the same second (two sockets of one player, an AI citizen of
    #: D-224) would pass an unlocked check together, so the row is taken first
    #: and every later order waits for this one to finish.
    await session.refresh(ship, with_for_update=True)
    if ship.docked_node_id is not None:
        raise Docked("корабль пристыкован: сначала отстыкуйтесь")
    running = await _passage_of(session, ship)
    if running is not None:
        goal = await session.get(Node, uuid.UUID(str(running.payload["to"])))
        where = f" в «{goal.name}»" if goal is not None else ""
        raise InFlight(f"корабль уже в рейсе{where}: до конца перехода он приказов не берёт")
    #: A yard, or the bare ground of a planet one lands anywhere on (D-233):
    #: Pyroxis has no port and can have none -- nothing is built there, so
    #: there is nothing to put a yard into, and a ship simply sets down.
    if not await world.has_station(session, port, SPACEPORT) and not await lands_anywhere(
        session, port
    ):
        raise NoPort(f"в «{port.name}» нет космодрома: причалить не к чему")
    if is_aboard(port):  # pragma: no cover -- a port is never a ship node
        raise NoPort("к борту не причаливают: цель рейса — космодром")
    #: A dark port takes nobody (D-231, D-232): the yard does not couple in a
    #: frozen node, and its beacon does not shine without power. Refused before
    #: the fuel is written off -- a ship must not set out for a place that will
    #: not have it.
    #:
    #: Asked **here and not on arrival**, deliberately: a passage takes hours,
    #: and a port that went dark while the ship was under way must not leave a
    #: crew in the void with no port at all. The gamble belongs to whoever cast
    #: off, and it is settled the way they left it.
    if not await beacon_lit(session, constants, port):
        raise NoPort(
            f"маяк «{port.name}» не светит: узел промёрз или верфь без энергии. "
            "Космодром работает, пока в его узле тепло и есть чем питать верфь — "
            "принести туда генерацию можно только пешком"
        )

    connector = await session.get(Node, ship.connector_node_id)
    if connector is None:  # pragma: no cover
        raise ShipError("у корабля нет коннектора")

    #: The time is settled **here**, at the moment of casting off, and is not
    #: recomputed afterwards: otherwise the sky would turn under a ship already
    #: under way and the passage would grow longer than the one paid for.
    table = await base_hours(session, constants, connector.planet, port.planet, at=moment)
    if table is None:
        raise TooFar(f"маршрута {connector.planet.value} — {port.planet.value} в мире нет")
    #: No route is closed by class (D-235): class is power and efficiency, and
    #: both are already priced -- a weak engine on a heavy hull flies longer
    #: (`passage_hours`) and burns more for every hour of it (`fuel_for`). What
    #: is still refused is having no engine at all, and having too little
    #: thrust to leave the ground; neither is a licence, both are physics.
    have_class = await engine_class(session, constants, ship)
    if have_class is None:
        raise NotEnoughThrust("на корабле нет ни одного двигателя")

    thrust_ratio = await ratio(session, constants, catalog, ship)
    floor = constants[R.SHIP_MIN_THRUST_RATIO]
    if thrust_ratio < floor:
        raise NotEnoughThrust(
            f"тяги {thrust_ratio:.2f} на килограмм при нужных {floor:.2f}: "
            "с такой массой рейс не начинается"
        )

    hours = passage_hours(constants, table, thrust_ratio)
    weight = await mass(session, constants, catalog, ship)
    #: By class, exactly as the console quoted it (`view.profile`) and as the
    #: turn-back charges it. Class is power and **efficiency** (D-235), and a
    #: passage that ignored it charged one price on the screen and another at
    #: the tanks.
    need_fuel = fuel_for(constants, weight, hours, klass=have_class)
    have_fuel = await fuel_aboard(session, ship)
    if have_fuel + _EPS < need_fuel:
        raise NoFuel(f"на рейс нужно {need_fuel:.1f} «{FUEL}», а на борту {have_fuel:.1f}")
    #: Burnt out of the tanks (D-230): the engines reach nothing else.
    burnt = await _spend(session, await fuel_stacks(session, ship), need_fuel)

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


async def recall(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    ship: Ship,
    *,
    now: datetime | None = None,
) -> Job:
    """Turn a passage back: the hull returns to the pier it cast off from (D-242).

    A passage used to be irreversible -- settled at the casting off and not
    recomputed, because a sky turning under a flying ship would make the trip
    longer than the one paid for (D-201). That rule is about the **sky**, not
    about the helm, and it survives here: the turn-back is not a recomputation
    of the passage but a second passage, priced by the only honest number there
    is -- how long this one has been under way.

    So the way home takes exactly as long as the way out has taken so far, and
    costs fuel by the same formula. Halfway to Aurora is a day back; an hour out
    is an hour back. Not enough fuel for it -- refused, and the hull flies on:
    a turn-back that emptied the tanks in the void would be the very trap the
    fuel rule exists against.
    """
    moment = now or datetime.now(UTC)
    await _commanded_by(session, body, ship)
    #: The job first, the hull second: the journal takes them in that order
    #: (`jobs._claim`), and two orders that disagree about it deadlock.
    running = await _passage_of(session, ship, lock=True)
    await session.refresh(ship, with_for_update=True)
    if running is None:
        raise Docked("корабль никуда не идёт: разворачивать нечего")
    #: A turn-back is not turned back. It is already going home, and the hours
    #: it counts are the hours of the passage it replaced -- counted afresh
    #: from itself they would be nought, and two clicks would bring a hull home
    #: from anywhere in the sky, instantly and for free.
    if running.payload.get("back"):
        raise InFlight(
            f"«{ship.name}» уже возвращается: разворачивать разворот некуда, дождитесь прихода"
        )
    home = None if ship.left_node_id is None else await session.get(Node, ship.left_node_id)
    if home is None:
        raise NoPort(
            f"неизвестно, откуда «{ship.name}» ушёл: развернуться не к чему, "
            "и рейс придётся довести до конца"
        )
    #: The same question `fly` asks of a destination, and for the same reason
    #: (D-232): a hull must not be sent where it will not be taken. A rescue
    #: that fails down a chain is not a rescue -- but a dark pier is not a
    #: chain, it is the answer, and the hull flies on to the port it aimed at.
    if not await beacon_lit(session, constants, home):
        raise NoPort(
            f"маяк «{home.name}» не светит: возвращаться некуда. Корабль дойдёт до цели рейса"
        )

    #: How long it has been flying is how long it has to fly back. Counted from
    #: the job that carries the passage: it was created at the casting off, and
    #: that is the one honest moment there is.
    gone = (moment - running.created_at).total_seconds() / SECONDS_PER_HOUR
    if gone <= 0:  # pragma: no cover -- a job is never created in the future
        gone = 0.0
    weight = await mass(session, constants, catalog, ship)
    have_class = await engine_class(session, constants, ship)
    need_fuel = fuel_for(constants, weight, gone, klass=have_class)
    have_fuel = await fuel_aboard(session, ship)
    if have_fuel + _EPS < need_fuel:
        raise NoFuel(
            f"на разворот нужно {need_fuel:.1f} «{FUEL}», а в баках {have_fuel:.1f}: "
            "с пустыми баками в пустоте не разворачиваются — идите до конца"
        )
    burnt = await _spend(session, await fuel_stacks(session, ship), need_fuel)

    #: The passage that was is over the moment the helm goes over. Its job is
    #: dropped rather than left to fire: two arrivals for one hull would set it
    #: down twice.
    running.state = JobState.CANCELLED
    running.finished_at = moment
    await session.flush()

    arrives = moment + timedelta(hours=gone)
    event = await events.record(
        session,
        EventKind.SHIP_RECALLED,
        actor_identity_id=body.identity_id,
        node_id=home.id,
        ship_id=str(ship.id),
        name=ship.name,
        to=home.key,
        hours=round(gone, ROUND_HOURS),
        fuel=burnt,
        arrives_at=arrives.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.SHIP_FLIGHT,
        arrives,
        #: Marked as the way back: a turn-back counts the hours of the passage
        #: it replaced, and has none of its own to count.
        payload={"ship": str(ship.id), "to": str(home.id), "back": True},
        dedup_key=f"ship.flight:{ship.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise ShipError("разворот уже поставлен")
    return job


async def _somewhere_on(session: AsyncSession, aim: Node, *, dice: random.Random) -> Node:
    """A node of this planet's surface, taken at random.

    Everything the planet takes a landing in is equal here: there are no piers,
    no berths and no lit beacons, so there is nothing to prefer. Falls back to
    the node aimed at if the planet somehow offers nothing -- an arrival must
    not be lost because a roll came up empty.
    """
    ground = [node for node in await open_landings(session) if node.planet is aim.planet]
    return dice.choice(sorted(ground, key=lambda one: one.key)) if ground else aim


@handler(JobKind.SHIP_FLIGHT)
async def arrived(session: AsyncSession, job: Job) -> None:
    """The passage is over: the edge to the port appears, and one may walk aboard again."""

    ship = await session.get(Ship, uuid.UUID(job.payload["ship"]), with_for_update=True)
    port = await session.get(Node, uuid.UUID(job.payload["to"]))
    if ship is None or port is None:  # pragma: no cover
        raise ShipError(f"рейс {job.id} ведёт в никуда")
    #: Already down. A hull is docked by exactly one arrival, and a second one
    #: -- a retry after a failure, a job that outlived a turn-back -- would lay
    #: a second gangway and moor a ship that is already moored.
    if ship.docked_node_id is not None:
        return
    #: On a planet one lands anywhere on there is no port to aim at, so the
    #: node is **rolled here, at the landing** (D-235): one sets down where the
    #: rock allows, not where it would be convenient. Seeded by the job, so a
    #: retry after a failure puts the ship down in the same place rather than
    #: teleporting it across the planet on the second attempt.
    if await lands_anywhere(session, port):
        port = await _somewhere_on(session, port, dice=random.Random(str(job.id)))
    connector = await session.get(Node, ship.connector_node_id)
    if connector is None:  # pragma: no cover
        raise ShipError("у корабля нет коннектора")

    #: The berth is taken on arrival, and it is whichever is free **there**:
    #: a ship does not carry its place from the port it left. On bare ground
    #: there are no berths to queue for (D-233): ships set down beside one
    #: another, and the walk down is always the same short one.
    ship.berth = 1 if await lands_anywhere(session, port) else await _free_berth(session, port)
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
