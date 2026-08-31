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
from src.engine import events, travel
from src.engine.jobs import enqueue, handler
from src.engine.ship._base import (
    _EPS,
    CLIMB,
    DESCENT,
    FUEL,
    PASSAGE,
    Docked,
    InFlight,
    NoFuel,
    NoLifeSupport,
    NoPort,
    NotEnoughThrust,
    ShipError,
    TooFar,
    _free_berth,
    _gangway_seconds,
    is_orbit,
    orbit_node_of,
)
from src.engine.ship.belonging import crew_of, nodes_of
from src.engine.ship.building import _planet_root, _spend
from src.engine.ship.command import _commanded_by, _landable, _will_take
from src.engine.ship.physics import (
    base_hours,
    climb_hours,
    engine_class,
    fall_hours,
    fuel_aboard,
    fuel_for,
    fuel_stacks,
    life_support,
    mass,
    passage_hours,
    ratio,
)
from src.engine.ship.view import lands_anywhere, open_landings
from src.models.event import EventKind
from src.models.identity import Body
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


async def _leaving(
    session: AsyncSession, constants: Constants, catalog: Catalog, ship: Ship
) -> tuple[Node, Node, float]:
    """What every leg begins with, and the refusals every leg shares (D-245).

    A passage between worlds used to be the only move there was, so its checks
    lived inside it. There are three moves now -- the climb to orbit, the
    crossing, the descent -- and they are one act done three times: the hull is
    standing somewhere, it faces outwards through its connector, it has thrust
    enough to move at all, and it holds no more people than its life support
    keeps breathing. Every one of them is known **before** the attempt.

    The hull's row is already held for update by the caller: what is read here
    is read under that lock.
    """
    if ship.docked_node_id is None:
        raise InFlight(key="ship-in-flight", ship=ship.name)
    running = await _passage_of(session, ship)
    if running is not None:  # pragma: no cover -- a moored hull carries no passage
        goal = await session.get(Node, uuid.UUID(str(running.payload["to"])))
        raise InFlight(
            key="ship-in-passage",
            known="true" if goal is not None else "false",
            goal="" if goal is None else goal.name,
        )

    here = await session.get(Node, ship.docked_node_id)
    connector = await session.get(Node, ship.connector_node_id)
    if here is None or connector is None:  # pragma: no cover
        raise ShipError(key="ship-no-connector-or-port")

    thrust_ratio = await ratio(session, constants, catalog, ship)
    floor = constants[R.SHIP_MIN_THRUST_RATIO]
    if thrust_ratio < floor:
        raise NotEnoughThrust(key="ship-not-enough-thrust", have=thrust_ratio, need=floor)
    crew = len(await crew_of(session, ship))
    holds = await life_support(session, constants, ship)
    if crew > holds:
        raise NoLifeSupport(key="ship-no-life-support", crew=crew, holds=holds)
    return here, connector, thrust_ratio


async def _burn(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    hours: float,
    reserve: float,
    refusal: str,
) -> tuple[float, float]:
    """Charge the tanks for a leg, having first checked the way out of its end.

    `hours` is what this leg costs; `reserve` is what the hull must still be
    able to fly afterwards and is **not** burnt. That second number is the
    whole of pillar P6 in arithmetic: an orbit has no bunker and a hull that
    reached one with dry tanks would hang there for ever, so a climb is refused
    without the descent behind it and a crossing without a descent at the far
    end. Nobody is sold a place they cannot leave -- with one exception the
    world means to keep: a planet whose beacons all go out **while the hull is
    crossing to it** takes the reserve away, and the hull hangs there. That is
    not this rule failing but D-232 working: Aurora's blackout is irreversible,
    and the planet is lost together with what is over it.

    `refusal` names which leg is asking (`climb`, `cross`, `land`, `turn-back`)
    -- a message variant rather than a sentence: the words are the locale's
    (D-251).

    Returns the mass burnt and the mass of the hull it was computed against --
    the caller writes both into the journal.
    """
    weight = await mass(session, constants, catalog, ship)
    klass = await engine_class(session, constants, ship)
    if klass is None:
        raise NotEnoughThrust(key="ship-no-engines")
    #: By class, exactly as the console quoted it (`view.profile`) and as the
    #: turn-back charges it. Class is power and **efficiency** (D-235), and a
    #: leg that ignored it charged one price on the screen and another at the
    #: tanks.
    need = fuel_for(constants, weight, hours, klass=klass)
    whole = fuel_for(constants, weight, hours + reserve, klass=klass)
    have = await fuel_aboard(session, ship)
    if have + _EPS < whole:
        raise NoFuel(key="ship-no-fuel", why=refusal, need=whole, goods=FUEL, have=have)
    #: Burnt out of the tanks (D-230): the engines reach nothing else.
    return await _spend(session, await fuel_stacks(session, ship), need), weight


async def _cast_off(session: AsyncSession, ship: Ship, here: Node, connector: Node) -> None:
    """Off the mooring: the edge goes, the berth goes, and where it stood is kept.

    `travel.disconnect` refuses if somebody is walking the gangway right now --
    one does not pull it from under a walker -- and that refusal travels up as
    it is.
    """
    await travel.disconnect(session, here, connector)
    ship.docked_node_id = None
    #: The berth is given back with the gangway: a ship under way holds no place
    #: at a pier, and the next arrival gets this one rather than a longer walk.
    ship.berth = None
    #: Where it came from, remembered (D-242). Casting off erases the other end
    #: of the leg, and "turn back" has to point somewhere: the job under way
    #: knows only where it is going.
    ship.left_node_id = here.id
    await session.flush()


async def _launch(
    session: AsyncSession,
    body: Body,
    ship: Ship,
    *,
    leg: str,
    frm: Node,
    to: Node,
    hours: float,
    fuel: float,
    weight: float,
    thrust_ratio: float,
    at: datetime,
) -> Job:
    """Write the leg into the journal and queue its arrival.

    One shape for all three legs, and `leg` is the only thing that tells them
    apart in the record: a climb, a crossing and a descent differ in what they
    cost, not in what happens at the end of them.
    """
    arrives = at + timedelta(hours=hours)
    event = await events.record(
        session,
        EventKind.SHIP_LAUNCHED,
        actor_identity_id=body.identity_id,
        node_id=frm.id,
        ship_id=str(ship.id),
        name=ship.name,
        leg=leg,
        to=to.key,
        hours=round(hours, ROUND_HOURS),
        fuel=fuel,
        mass=round(weight, ROUND_MASS),
        ratio=round(thrust_ratio, ROUND_RATIO),
        arrives_at=arrives.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.SHIP_FLIGHT,
        arrives,
        payload={"ship": str(ship.id), "to": str(to.id), "leg": leg},
        dedup_key=f"ship.flight:{ship.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise ShipError(key="ship-passage-already-queued")
    return job


async def ascend(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    ship: Ship,
    *,
    now: datetime | None = None,
) -> Job:
    """Climb from a spaceport to the orbit of the planet under it (D-245).

    The step that used to cost nothing. Casting off was instant and free, while
    coming back down to the very port one had left was priced as a whole
    passage between worlds -- so leaving a planet was cheaper than returning to
    it, which is the wrong way round for every world there is.

    Now both ends of a planet cost what its **gravity** says they cost:
    `planet.gravity` times the vault's base, stretched by thrust against mass
    like any other leg. Pyroxis is dear to leave and dear to come down onto;
    Aurora is cheap at both ends and closed at one by its dark beacons (D-232).

    Cancellable, and that is the point of making it a leg rather than an
    instant: `recall` puts the hull back on the very pad it lifted from.
    """
    moment = now or datetime.now(UTC)
    await _commanded_by(session, body, ship)
    #: Held while it is decided, as in every order: this and the crossing are
    #: writes into the same row, and two orders given in the same second (two
    #: sockets of one player, an AI citizen of D-224) would both pass an
    #: unlocked check and burn the tanks twice.
    await session.refresh(ship, with_for_update=True)
    here, connector, thrust_ratio = await _leaving(session, constants, catalog, ship)
    if is_orbit(here):
        raise InFlight(key="ship-already-in-orbit", ship=ship.name)
    orbit = await orbit_node_of(session, here.planet)
    if orbit is None:  # pragma: no cover -- the seed lays one per planet
        raise NoPort(key="ship-planet-has-no-orbit", planet=here.planet.value)

    climb = climb_hours(constants, here.planet, thrust_ratio)
    burnt, weight = await _burn(
        session,
        constants,
        catalog,
        ship,
        hours=climb,
        #: The way back down onto this same planet. Not burnt -- kept.
        reserve=fall_hours(constants, here.planet, thrust_ratio),
        refusal="climb",
    )
    await _cast_off(session, ship, here, connector)
    return await _launch(
        session,
        body,
        ship,
        leg=CLIMB,
        frm=here,
        to=orbit,
        hours=climb,
        fuel=burnt,
        weight=weight,
        thrust_ratio=thrust_ratio,
        at=moment,
    )


async def fly(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    ship: Ship,
    target: Node,
    *,
    now: datetime | None = None,
) -> Job:
    """Cross to another planet's orbit. Orbit to orbit, never ground to ground.

    The passage the sky prices (D-201): its length is `ship.base_hours` for the
    pair of planets at this very hour, and it is settled **here**, at the
    casting off, never recomputed -- a sky turning under a ship already under
    way would make the passage longer than the one paid for.

    What D-245 changed is only its ends. A crossing starts and finishes in
    orbit, so the ground is one more leg away at each end, and the way from
    Terra to Aurora is three moves: up, across, down -- with the spaceport
    chosen only once the hull is hanging over the planet it picked.

    The route's class is decided by the **weakest** engine aboard (D-037): one
    poor engine in the cluster holds the cluster back.
    """
    moment = now or datetime.now(UTC)
    await _commanded_by(session, body, ship)
    await session.refresh(ship, with_for_update=True)
    here, connector, thrust_ratio = await _leaving(session, constants, catalog, ship)
    if not is_orbit(here):
        raise Docked(key="ship-cross-from-orbit", ship=ship.name)
    if not is_orbit(target):
        raise NoPort(key="ship-cross-to-orbit", node=target.name)
    if target.planet is here.planet:
        raise TooFar(key="ship-already-over-planet", ship=ship.name)
    #: Every question a mooring is asked, and one more the others are not: a
    #: planet whose beacons have all gone out is a planet one may reach and
    #: never leave the orbit of (D-232). The hull would hang there with fuel for
    #: a descent and nowhere to spend it, which is the trap the fuel rule exists
    #: against (pillar P6) -- so the crossing is refused at this end, while
    #: there is still a choice to make.
    await _will_take(session, constants, target, why="dock")
    if not await _landable(session, constants, target.planet):
        raise NoPort(key="ship-nowhere-to-land", node=target.name)

    #: The sky, asked once and written into the passage.
    table = await base_hours(session, constants, here.planet, target.planet, at=moment)
    if table is None:
        raise TooFar(
            key="ship-no-such-route",
            planet_from=here.planet.value,
            planet_to=target.planet.value,
        )
    #: No route is closed by class (D-235): class is power and efficiency, and
    #: both are already priced -- a weak engine on a heavy hull flies longer
    #: (`passage_hours`) and burns more for every hour of it (`fuel_for`). What
    #: is still refused is having no engine at all, and having too little thrust
    #: to move; neither is a licence, both are physics.
    hours = passage_hours(constants, table, thrust_ratio)
    burnt, weight = await _burn(
        session,
        constants,
        catalog,
        ship,
        hours=hours,
        #: The descent at the far end, kept back the way the climb keeps its own.
        reserve=fall_hours(constants, target.planet, thrust_ratio),
        refusal="cross",
    )
    await _cast_off(session, ship, here, connector)
    return await _launch(
        session,
        body,
        ship,
        leg=PASSAGE,
        frm=here,
        to=target,
        hours=hours,
        fuel=burnt,
        weight=weight,
        thrust_ratio=thrust_ratio,
        at=moment,
    )


async def land(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    ship: Ship,
    port: Node,
    *,
    now: datetime | None = None,
) -> Job:
    """Come down from orbit onto a spaceport of the planet below (D-245).

    The half of the journey that had no button at all: a hull was aimed at a
    port from wherever it happened to be and set itself down at the end of one
    passage. Now the port is chosen **over the planet**, with the hull already
    hanging above it -- which is the moment a crew actually knows what it is
    choosing between, and the moment a dark beacon actually matters.

    Priced by the planet's gravity, like the climb and a little cheaper than
    it: coming down, the weight one climbed against is on the ship's side.
    """
    moment = now or datetime.now(UTC)
    await _commanded_by(session, body, ship)
    await session.refresh(ship, with_for_update=True)
    here, connector, thrust_ratio = await _leaving(session, constants, catalog, ship)
    if not is_orbit(here):
        raise Docked(key="ship-already-landed", ship=ship.name)
    #: An orbit is not a pad. `_will_take` says yes to every orbital node --
    #: space needs no yard and has no beacon -- so without this line a descent
    #: aimed at the very orbit the hull is moored to passed: the trap was
    #: unmoored, charged a descent and moored again, one leg's fuel poorer and
    #: below the reserve that keeps an orbit leavable.
    if is_orbit(port):
        raise NoPort(key="ship-land-not-into-orbit", node=port.name)
    if port.planet is not here.planet:
        raise TooFar(key="ship-land-other-planet", node=port.name)
    await _will_take(session, constants, port, why="land")

    fall = fall_hours(constants, here.planet, thrust_ratio)
    burnt, weight = await _burn(
        session,
        constants,
        catalog,
        ship,
        hours=fall,
        #: Nothing kept back: the ground is the one place a hull may stand with
        #: dry tanks. Fuel is walked to a pier; it is not walked to an orbit.
        reserve=0.0,
        refusal="land",
    )
    await _cast_off(session, ship, here, connector)
    return await _launch(
        session,
        body,
        ship,
        leg=DESCENT,
        frm=here,
        to=port,
        hours=fall,
        fuel=burnt,
        weight=weight,
        thrust_ratio=thrust_ratio,
        at=moment,
    )


async def recall(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    ship: Ship,
    *,
    now: datetime | None = None,
) -> Job:
    """Turn a leg back: the hull returns to where it cast off from (D-242).

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

    Any leg, and the climb most of all (D-245): "подняться на орбиту" is an
    order one may take back, and taking it back sets the hull down on the very
    pad it lifted from.
    """
    moment = now or datetime.now(UTC)
    await _commanded_by(session, body, ship)
    #: The job first, the hull second: the journal takes them in that order
    #: (`jobs._claim`), and two orders that disagree about it deadlock.
    running = await _passage_of(session, ship, lock=True)
    await session.refresh(ship, with_for_update=True)
    if running is None:
        raise Docked(key="ship-not-in-passage")
    #: A turn-back is not turned back. It is already going home, and the hours
    #: it counts are the hours of the leg it replaced -- counted afresh from
    #: itself they would be nought, and two clicks would bring a hull home from
    #: anywhere in the sky, instantly and for free.
    if running.payload.get("back"):
        raise InFlight(key="ship-already-turning-back", ship=ship.name)
    home = None if ship.left_node_id is None else await session.get(Node, ship.left_node_id)
    if home is None:
        raise NoPort(key="ship-no-home-to-turn-to", ship=ship.name)
    #: The **same** question every destination is asked, all of it (D-232): a
    #: hull must not be sent where it will not be taken. A rescue that fails
    #: down a chain is not a rescue -- but a pier with its yard carried off is
    #: not a chain, it is the answer, and the hull flies on to the port it aimed
    #: at, which was checked when it was aimed at.
    await _will_take(session, constants, home, why="turn-back")

    #: How long it has been flying is how long it has to fly back. Counted from
    #: the job that carries the leg: it was created at the casting off, and that
    #: is the one honest moment there is.
    #:
    #: **Never less than a landing, though**, when what it turns back to is
    #: ground. Turned round in the first minute a hull has gone nowhere, and the
    #: arithmetic alone would put it back on the pad at once and for nothing --
    #: which is a way to skip the descent every landing costs (D-245). However
    #: close it still has to come down, and coming down is a leg like any other.
    #: Turning back into an **orbit** has no such floor: an orbit is where the
    #: hull already was, and nothing has been skipped by going back to it.
    thrust_ratio = await ratio(session, constants, catalog, ship)
    down = fall_hours(constants, home.planet, thrust_ratio)
    floor = 0.0 if is_orbit(home) else down
    flown = max(0.0, (moment - running.created_at).total_seconds() / SECONDS_PER_HOUR)
    gone = max(flown, floor)
    burnt, _ = await _burn(
        session,
        constants,
        catalog,
        ship,
        hours=gone,
        #: A turn-back is a leg like the others, and it keeps what the others
        #: keep: an orbit has no bunker, so coming home to one is refused
        #: without the descent still in the tanks. Turned back into an orbit
        #: without it, a hull would be exactly where `_burn` exists to stop it
        #: from being -- and the way there is short: cross out, turn round in
        #: the first minute, and the reserve the crossing kept is spent on a
        #: planet whose descent costs more than the one it was measured for.
        reserve=down if is_orbit(home) else 0.0,
        refusal="turn-back",
    )

    #: The leg that was is over the moment the helm goes over. Its job is
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
        #: Marked as the way back: a turn-back counts the hours of the leg it
        #: replaced, and has none of its own to count.
        payload={"ship": str(ship.id), "to": str(home.id), "back": True},
        dedup_key=f"ship.flight:{ship.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise ShipError(key="ship-turn-back-already-queued")
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
        raise ShipError(key="ship-passage-nowhere", job=str(job.id))
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
    #: A turn-back named its pier on the button -- "Развернуться в «Плато
    #: Наковальни»" -- and rolling a different field under it would make the
    #: interface a liar. Only a passage aimed at a planet is rolled (D-235).
    if await lands_anywhere(session, port) and not job.payload.get("back"):
        port = await _somewhere_on(session, port, dice=random.Random(str(job.id)))
    connector = await session.get(Node, ship.connector_node_id)
    if connector is None:  # pragma: no cover
        raise ShipError(key="ship-no-connector")

    #: The berth is taken on arrival, and it is whichever is free **there**:
    #: a ship does not carry its place from the port it left. On bare ground
    #: there are no berths to queue for (D-233), and in orbit there is no pier
    #: to queue at (D-245): hulls hang beside one another, and the walk out is
    #: the same short spacewalk however many are parked. Numbered berths would
    #: have made the twentieth hull over Terra climb a gangway twenty times the
    #: first one's, for a pier that does not exist.
    ship.berth = (
        1
        if is_orbit(port) or await lands_anywhere(session, port)
        else await _free_berth(session, port)
    )
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
