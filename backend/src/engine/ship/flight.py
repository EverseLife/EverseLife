# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: the legs by the hour -- the climb to orbit, the descent to a pad,
the turn-back -- and the arrival that ends them (D-245, D-354).

Split out of `engine/ship.py` along its sections (review 2026-08-23, wave 3).

A climb ends in orbit and a descent starts from one, and since D-354 an
orbit is no node: the climb puts the hull into the sky over the meridian of
the pad it left, a body with a place and a speed, and the descent is asked
of a hull the sky says is in orbit round the planet of the pad.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import sky
from src.constants import Catalog, Constants, current
from src.constants import registry as R
from src.engine import climate, estate, events, travel, world
from src.engine.jobs import enqueue, handler
from src.engine.ship import course, fate, hold, meet, sim
from src.engine.ship._base import (
    _EPS,
    CLIMB,
    DESCENT,
    FUEL,
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
)
from src.engine.ship.building import moor_to
from src.engine.ship.command import _commanded_by, _still_commanded_by, _will_take
from src.engine.ship.physics import (
    _sphere,
    burn_checked,
    climb_hours,
    efficiency,
    engine_class,
    fall_hours,
    fuel_for,
    life_support,
    mass,
    ratio,
    sky_days,
)
from src.engine.ship.view import lands_anywhere
from src.models.event import EventKind
from src.models.identity import Body
from src.models.job import Job, JobKind, JobState
from src.models.ship import Ship
from src.models.world import Layer, Node, Planet, Surface
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

    return here, connector, await _fit(session, constants, catalog, ship)


async def _fit(session: AsyncSession, constants: Constants, catalog: Catalog, ship: Ship) -> float:
    """Whether the hull can move at all, wherever it is: thrust enough to tear
    off, and a system that breathes. Returns the thrust-to-mass.

    Asked of a moored hull by `_leaving`, and of one in the sky by `fly` and
    `land` (D-289, D-354): a hull lays a course or comes down by the same two
    rules it left the pier by.
    """
    thrust_ratio = await ratio(session, constants, catalog, ship)
    floor = constants[R.SHIP_MIN_THRUST_RATIO]
    if thrust_ratio < floor:
        raise NotEnoughThrust(key="ship-not-enough-thrust", have=thrust_ratio, need=floor)
    #: A system, not a number of people (D-288): the crew has no ceiling, the
    #: air on the system's line is the ceiling, and the console shows the
    #: hours. What is refused is a hull that breathes for nobody at all.
    if not await life_support(session, ship):
        raise NoLifeSupport(key="ship-no-life-support")
    return thrust_ratio


async def _burn(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    hours: float,
    refusal: str,
    dv: float | None = None,
) -> tuple[float, float]:
    """Charge the tanks for a leg.

    `hours` is what this leg costs, and that is all that is checked: nothing
    is kept back for the way out of its end any more (D-289). The reserve
    that used to be refused here -- the descent behind a climb, the descent
    at the far end of a crossing (pillar P6, D-245) -- is the console's
    warning now: a hull that climbs dry sits on its circle, moored and
    stable, and fuel is what fetches it (wave 3's rendezvous).

    `refusal` names which leg is asking (`climb`, `cross`, `land`, `turn-back`)
    -- a message variant rather than a sentence: the words are the locale's
    (D-251).

    `dv` is given for a crossing between worlds (D-271): the passage pays for
    speed rather than for hours, while the reserve at its end is a descent and
    still pays per hour. Without it the leg is priced by `hours` alone.

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
    if dv is None:
        need = fuel_for(constants, weight, hours, klass=klass)
    else:
        need = course.fuel_for_speed(constants, weight, dv, efficiency=efficiency(constants, klass))
    whole = need
    #: In reference units on both sides (D-252): the need is quoted in
    #: rocket-fuel units, and the tanks answer with what their kinds are
    #: worth -- kerosene closes more of it per unit than it shows. Checked
    #: and burnt under one lock (`burn_checked`): a tank drained between the
    #: two would otherwise let the leg fly on fuel it never paid. Burnt off
    #: the engines' lines (D-288): the vessels named when the line was
    #: drawn, and nothing without one (as amended 2026-09-04).
    burnt, have = await burn_checked(session, constants, catalog, ship, need=need, whole=whole)
    if burnt <= 0 and have + _EPS < whole:
        raise NoFuel(key="ship-no-fuel", why=refusal, need=whole, goods=FUEL, have=have)
    return burnt, weight


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
    over: Node | None = None,
) -> Job:
    """Write the leg into the journal and queue its arrival.

    One shape for all three legs, and `leg` is the only thing that tells them
    apart in the record: a climb, a crossing and a descent differ in what they
    cost, not in what happens at the end of them. A crossing is no leg any
    more (D-289): it is an order on the hull's row, and no job is queued.
    """
    arrives = at + timedelta(hours=hours)
    payload: dict[str, object] = {"ship": str(ship.id), "to": str(to.id), "leg": leg}
    #: The pad whose meridian a hull ending in orbit comes out over (D-354):
    #: the one it lifted from, or the one it was coming down to.
    if over is not None:
        payload["over"] = str(over.id)
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
        payload=payload,
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
    """Climb from a spaceport into orbit round the planet under it (D-245,
    D-354).

    The step that used to cost nothing. Casting off was instant and free, while
    coming back down to the very port one had left was priced as a whole
    passage between worlds -- so leaving a planet was cheaper than returning
    to it, which is the wrong way round for every world there is.

    Now both ends of a planet cost what its **gravity** says they cost: the
    world's own pull -- `planet.mass` over `planet.radius` squared, D-320 --
    times the vault's base, stretched by thrust against mass like any other
    leg. Pyroxis is dear to leave and dear to come down onto;
    Aurora is cheap at both ends and closed at one by its dark beacons (D-232).

    The leg ends in the sky, not at a node: on the parking circle, over the
    meridian of this very pad at the hour of arrival (`arrived`). Cancellable,
    and that is the point of making it a leg rather than an instant: `recall`
    puts the hull back on the very pad it lifted from.
    """
    moment = now or datetime.now(UTC)
    await _commanded_by(session, body, ship)
    #: Held while it is decided, as in every order: this and the crossing are
    #: writes into the same row, and two orders given in the same second (two
    #: sockets of one player, an AI citizen of D-224) would both pass an
    #: unlocked check and burn the tanks twice.
    await session.refresh(ship, with_for_update=True)
    here, connector, thrust_ratio = await _leaving(session, constants, catalog, ship)
    sphere = await _sphere(session, here.planet)
    world_sky = await sim.system(session, constants)
    if sphere is None or here.planet.value not in {one.key for one in world_sky.bodies}:
        #: A world the sky does not run -- a test world laid without its
        #: planet -- has no orbit to climb into.
        raise NoPort(key="ship-planet-has-no-orbit", planet=here.planet.value)

    climb = climb_hours(constants, here.planet, thrust_ratio)
    burnt, weight = await _burn(
        session,
        constants,
        catalog,
        ship,
        hours=climb,
        #: Nothing kept back for the way down (D-289): the descent is the
        #: console's warning, not the engine's refusal. A hull that climbs
        #: dry hangs in orbit -- stable, and fetched by fuel from a hull
        #: come alongside.
        refusal="climb",
    )
    await _cast_off(session, ship, here, connector)
    return await _launch(
        session,
        body,
        ship,
        leg=CLIMB,
        frm=here,
        to=sphere,
        hours=climb,
        fuel=burnt,
        weight=weight,
        thrust_ratio=thrust_ratio,
        at=moment,
        over=here,
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
    """Come down from orbit onto a spaceport of the planet below (D-245,
    D-354).

    The port is chosen **over the planet**, with the hull already hanging
    above it -- which is the moment a crew actually knows what it is choosing
    between, and the moment a dark beacon actually matters.

    Asked of a hull the sky says is in orbit round the port's planet
    (`sim.orbit_of`), and close in: the whole orbit inside the window the
    helm catches arrivals in (`orbit.capture_radii`). A hull on a wider one
    is sent to the planet first, and the helm brings it down to the circle.
    Priced by the planet's gravity, like the climb and a little cheaper than
    it: coming down, the weight one climbed against is on the ship's side.
    """
    moment = now or datetime.now(UTC)
    await _commanded_by(session, body, ship)
    await session.refresh(ship, with_for_update=True)
    if ship.docked_node_id is not None:
        raise Docked(key="ship-already-landed", ship=ship.name)
    if ship.lost_at is not None:
        raise ShipError(key="ship-lost", ship=ship.name)
    if ship.course or await _passage_of(session, ship) is not None:
        raise InFlight(key="ship-in-flight", ship=ship.name)
    if ship.held_ship_id is not None:
        #: On a hold the hull's place is the other hull's: read afresh.
        await session.get(Ship, ship.held_ship_id, populate_existing=True)
    held = await sim.orbit_of(session, constants, ship)
    if held is None:
        raise InFlight(key="ship-not-in-orbit", ship=ship.name)
    if port.planet.value != held.body.key:
        raise TooFar(key="ship-land-other-planet", node=port.name)
    if held.far > sky.capture_of(await sim.system(session, constants), held.body):
        raise TooFar(key="ship-orbit-too-high", ship=ship.name)
    await _will_take(session, constants, ship, port, why="land")
    thrust_ratio = await _fit(session, constants, catalog, ship)

    fall = fall_hours(constants, port.planet, thrust_ratio)
    burnt, weight = await _burn(
        session,
        constants,
        catalog,
        ship,
        hours=fall,
        refusal="land",
    )
    #: Whoever holds on to this hull is let go of first, from the state they
    #: shared, and a hold or a docking of its own comes off (D-289, wave 3):
    #: into the air a hull goes alone.
    await hold.release_holders(
        session, constants, await sim.system(session, constants), ship, now=moment
    )
    await meet.let_go(session, constants, ship)
    #: Out of the sky and into the air (D-289): a descent is a leg by the
    #: hour, and the sky has no state for a hull under one.
    ship.sky_at = None
    ship.sky_x = ship.sky_y = ship.sky_vx = ship.sky_vy = None
    ship.forecast = None
    ship.held_ship_id = None
    await session.flush()
    frm = await _sphere(session, port.planet)
    assert frm is not None  # the sky ran the planet a moment ago
    return await _launch(
        session,
        body,
        ship,
        leg=DESCENT,
        frm=frm,
        to=port,
        hours=fall,
        fuel=burnt,
        weight=weight,
        thrust_ratio=thrust_ratio,
        at=moment,
        over=port,
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
    pad it lifted from. A descent taken back puts the hull into orbit again,
    over the pad it was coming down to (D-354).
    """
    moment = now or datetime.now(UTC)
    await _commanded_by(session, body, ship)
    #: The job first, the hull second: the journal takes them in that order
    #: (`jobs._claim`), and two orders that disagree about it deadlock.
    running = await _passage_of(session, ship, lock=True)
    await session.refresh(ship, with_for_update=True)
    #: And the captain's row last of the three: a hull's row comes before the
    #: bodies of its crew (`belonging.lock_crew`), and the job comes before
    #: the hull, so the turn-back cannot take its pair in the door the way the
    #: other orders do (`api.commands.transport._ordered`).
    body = await _still_commanded_by(session, body, ship)
    #: A crossing under the sky is not turned back (D-289, 2026-09-04): it is
    #: cancelled into a coast (`crossing.cancel`) or replaced by another
    #: order; only the tabled legs -- the climb and the descent -- come back.
    if running is None and ship.course and ship.docked_node_id is None:
        raise InFlight(key="ship-course-not-turned", ship=ship.name)
    if running is None:
        raise Docked(key="ship-not-in-passage")
    #: A turn-back is not turned back. It is already going home, and the hours
    #: it counts are the hours of the leg it replaced -- counted afresh from
    #: itself they would be nought, and two clicks would bring a hull home from
    #: anywhere in the sky, instantly and for free.
    if running.payload.get("back"):
        raise InFlight(key="ship-already-turning-back", ship=ship.name)
    over: Node | None = None
    if running.payload.get("leg") == DESCENT:
        #: Back up: the orbit it came down from, over the pad it was aiming
        #: at (D-354). The sky above a planet is no node, so the leg's own
        #: start -- the planet -- is where it goes back to.
        home = await session.get(Node, uuid.UUID(str(running.payload["to"])))
        over = home
        home = None if home is None else await _sphere(session, home.planet)
    else:
        home = None if ship.left_node_id is None else await session.get(Node, ship.left_node_id)
    if home is None:
        raise NoPort(key="ship-no-home-to-turn-to", ship=ship.name)
    #: The **same** question every destination is asked, all of it (D-232): a
    #: hull must not be sent where it will not be taken. A rescue that fails
    #: down a chain is not a rescue -- but a pier with its yard carried off is
    #: not a chain, it is the answer, and the hull flies on to the port it aimed
    #: at, which was checked when it was aimed at. The sky takes every hull.
    if over is None:
        await _will_take(session, constants, ship, home, why="turn-back")

    #: How long it has been flying is how long it has to fly back, and nothing
    #: else -- D-242's own words, "новых чисел нет". Counted from the job that
    #: carries the leg: it was created at the casting off, and that is the one
    #: honest moment there is.
    #:
    #: There used to be a floor here, a whole landing's worth, against
    #: "отстыковаться, нацелиться куда угодно, развернуться" -- being down
    #: again before the gauge moved (D-242, the review clarification). D-289
    #: took that path away: a crossing under the sky is not turned back at all
    #: any more (`ship-course-not-turned` above), so the only leg that turns
    #: back to ground is the climb, and a climb turned back in its first
    #: seconds is a hull that never left the pad. The floor was charging two
    #: and a half hours of descent for ten seconds of climb, which is not a
    #: rule anybody could read off the world.
    flown = max(0.0, (moment - running.created_at).total_seconds() / SECONDS_PER_HOUR)
    gone = flown
    #: A crossing paid for speed, not for hours (D-271), and the way home is a
    #: second arc that costs what the first did: the same delta-v, however far
    #: along it the helm went over. Legs to and from the ground carry no
    #: delta-v and keep paying by the hour.
    paid = running.payload.get("dv")
    burnt, _ = await _burn(
        session,
        constants,
        catalog,
        ship,
        hours=gone,
        dv=None if paid is None else float(paid),
        #: A turn-back is a leg like the others, and like the others it keeps
        #: nothing back (D-289): turned back into an orbit short of the
        #: descent, a hull sits on its circle and waits for fuel.
        refusal="turn-back",
    )

    #: The leg that was is over the moment the helm goes over. Its job is
    #: dropped rather than left to fire: two arrivals for one hull would set it
    #: down twice.
    running.state = JobState.CANCELLED
    running.finished_at = moment
    await session.flush()

    arrives = moment + timedelta(hours=gone)
    home_arc: dict[str, object] = {}
    if paid is not None:
        #: Only the part of the arc the hull has actually flown, reversed:
        #: the way back starts where the helm went over, not at the far end.
        whole = (running.run_at - running.created_at).total_seconds()
        share = 1.0 if whole <= 0 else min(1.0, flown * SECONDS_PER_HOUR / whole)
        home_arc = {
            "dv": paid,
            "arc": list(reversed(course.flown(running.payload.get("arc") or [], share))),
        }
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
        #: replaced, and has none of its own to count. The arc goes home with
        #: it, reversed: the map draws the hull back along the way it came.
        payload={
            "ship": str(ship.id),
            "to": str(home.id),
            "back": True,
            **({"over": str(over.id)} if over is not None else {}),
            **home_arc,
        },
        dedup_key=f"ship.flight:{ship.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise ShipError(key="ship-turn-back-already-queued")
    return job.run_at


@handler(JobKind.SHIP_FLIGHT)
async def arrived(session: AsyncSession, job: Job) -> None:
    """The leg is over: in orbit over the pad's meridian (D-354), or down on a
    pad with the gangway laid and the way aboard open again."""

    ship = await session.get(Ship, uuid.UUID(job.payload["ship"]), with_for_update=True)
    port = await session.get(Node, uuid.UUID(job.payload["to"]))
    if ship is None or port is None:  # pragma: no cover
        raise ShipError(key="ship-passage-nowhere", job=str(job.id))
    #: Already down, or already in the sky. A hull ends a leg by exactly one
    #: arrival, and a second one -- a retry after a failure, a job that
    #: outlived a turn-back -- would lay a second gangway or put the hull on a
    #: second orbit.
    if ship.docked_node_id is not None or ship.sky_at is not None:
        return
    if port.layer is Layer.SPACE:
        await _into_orbit(session, ship, port, job)
        return
    #: Where the order said, and nowhere else (D-319). A planet one lands
    #: anywhere on used to roll the node at the landing (D-235); now the crew
    #: picks it from orbit -- the globe under the hull is the landing picker,
    #: and a node with no room for the hull refused at the choice -- so an
    #: arrival that set the hull down somewhere else would land it on ground
    #: nobody asked about and that may have no room.
    connector = await session.get(Node, ship.connector_node_id)
    if connector is None:  # pragma: no cover
        raise ShipError(key="ship-no-connector")
    #: The pad's row, as whoever spends its ground takes it (`require_port`):
    #: setting down moves the hull from the hulls on their way to the hulls
    #: standing, and `estate.free_ground` counts the two in two statements --
    #: an order counting between them would read the hull in neither and give
    #: its place away. The gangway's foreign key used to make this wait by
    #: accident, while the plot was held `FOR UPDATE`.
    await estate.hold_ground(session, port)

    #: The berth is taken on arrival, and it is whichever is free **there**:
    #: a ship does not carry its place from the port it left. On bare ground
    #: there are no berths to queue for (D-233).
    ship.berth = 1 if await lands_anywhere(session, port) else await _free_berth(session, port)
    await travel.connect(
        session,
        port,
        connector,
        base_seconds=_gangway_seconds(current(), ship.berth),
        surface=Surface.PAVED,
    )
    ship.docked_node_id = port.id
    await moor_to(session, ship, port)
    #: Down on a pad there is no sky and no state.
    ship.sky_at = None
    ship.sky_x = ship.sky_y = ship.sky_vx = ship.sky_vy = None
    ship.course = None
    ship.forecast = None
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


async def _into_orbit(session: AsyncSession, ship: Ship, sphere: Node, job: Job) -> None:
    """A climb, or a descent turned back, has ended in orbit (D-354): the hull
    is put on the parking circle over the meridian of the pad the leg began
    or was bound for, at the hour it arrives -- at local noon on the side of
    the star, at midnight on the far side -- going round the way the planet
    turns. Not a place spun off the hull's id any more: two hulls that climb
    from one pad at one hour come out side by side, and a hull already in
    orbit is met by climbing when it passes overhead."""
    constants = current()
    world_sky = await sim.system(session, constants)
    try:
        body = world_sky.body(sphere.planet.value)
    except KeyError:  # pragma: no cover -- `ascend` asked the sky before the climb
        raise ShipError(key="ship-planet-has-no-orbit", planet=sphere.planet.value) from None
    over_id = job.payload.get("over")
    over = None if over_id is None else await session.get(Node, uuid.UUID(str(over_id)))
    t = await sky_days(session, job.run_at)
    angle = await meridian(session, constants, body, over, t=t, at=job.run_at)
    r, v = sky.parking(world_sky, body, t, angle)
    here = (float(r[0, 0]), float(r[0, 1]))
    speed = (float(v[0, 0]), float(v[0, 1]))
    await sim.into_orbit(session, ship, sphere, r=here, v=speed, now=job.run_at)
    verdict = await fate.book_loss(
        session, constants, ship, world_sky, now=job.run_at, t=t, r=here, v=speed
    )
    sim._keep_forecast(ship, verdict, now=job.run_at, t=t)
    await events.record(
        session,
        EventKind.SHIP_IN_ORBIT,
        actor_identity_id=ship.owner_identity_id,
        node_id=ship.connector_node_id,
        ship_id=str(ship.id),
        name=ship.name,
        planet=sphere.planet.value,
    )
    await session.flush()


async def meridian(
    session: AsyncSession,
    constants: Constants,
    body: sky.Body,
    over: Node | None,
    *,
    t: float,
    at: datetime,
) -> float:
    """Where in the sky, round its planet, the meridian of `over` points at
    the moment `at`, radians -- the angle a hull climbing from it comes out at
    (D-354). At the place's noon it points at the star, and it turns with the
    planet's day (`climate.day_phase`); a place with no longitude is read on
    the planet's own meridian."""
    p, _ = sky.place(body, t)
    toward_star = math.atan2(-float(p[0, 1]), -float(p[0, 0]))
    planet = Planet(body.key)
    longitude = 0.0 if over is None else climate.longitude_of(over)
    phase = climate.day_phase(
        constants, planet, await world.epoch(session), at, longitude=longitude
    )
    #: Half a day off noon is midnight: the angle turns a whole circle a day.
    return toward_star + math.tau * phase - math.pi
