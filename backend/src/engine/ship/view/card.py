# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The ship's card: one profile that names the hull's nodes, engines, mass,
tanks and crew -- everything the console shows about the vessel itself.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import sky
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import world
from src.engine.ship import course, sighting, sim
from src.engine.ship._base import (
    ADRIFT,
    AT_PORT,
    BRIDGE,
    IN_ORBIT,
    LOST,
    UNDER_WAY,
    NoArc,
    ShipError,
    is_orbit,
    orbit_key,
    orbit_node_of,
)
from src.engine.ship.belonging import crew_of, nodes_of
from src.engine.ship.physics import (
    _things,
    climb_hours,
    efficiency,
    engine_class,
    engines,
    fall_hours,
    fuel_aboard,
    fuel_for,
    fuel_worth,
    life_support,
    mass,
    mass_parts,
    ratio,
    sky_days,
    thrust,
)
from src.engine.ship.view.sight import _oxygen
from src.engine.ship.view.sky import _flight, _open_planets, lit_ports
from src.models.ship import Ship
from src.models.world import Node, Planet
from src.runtime import SHIP_GRID, SHIP_GRID_REACH
from src.units import (
    ROUND_DV,
    ROUND_HOURS,
    ROUND_MASS,
    ROUND_RATIO,
    ROUND_TRACE,
)


async def profile(
    session: AsyncSession, constants: Constants, catalog: Catalog, ship: Ship
) -> dict:
    """The ship's summary: thrust, mass, and the price of every route from here.

    Shown **before** undocking, deliberately (D-202): a refusal by mass must not
    be a surprise sprung after the hold is loaded. Remote, like every reading:
    information travels the Net, matter requires presence (D-044).
    """
    nodes = await nodes_of(session, ship)
    #: The hold is read once and asked every question: seven readings of the
    #: same rooms were the price of the summary before (review 2026-08-23).
    things = await _things(session, ship)
    consoles = frozenset(world.station_names(BRIDGE))
    weight = await mass(session, constants, catalog, ship, things=things)
    pull = await thrust(session, constants, ship, things=things)
    thrust_ratio = pull / weight if weight > 0 else 0.0
    have_class = await engine_class(session, constants, ship, things=things)
    crew = len(await crew_of(session, ship))
    connector = await session.get(Node, ship.connector_node_id)
    docked = None if ship.docked_node_id is None else await session.get(Node, ship.docked_node_id)
    home = None if ship.left_node_id is None else await session.get(Node, ship.left_node_id)

    #: The prices are for **this** moment: the sky turns, and a route quoted an
    #: hour ago is not the route one gets. The player sees what setting out now
    #: would cost, and the window they may prefer to wait for is on the chart.
    moment = datetime.now(UTC)
    planet = Planet.TERRA if connector is None else connector.planet
    #: Where the hull is in its journey (D-245, D-289). Five stages, and each
    #: offers a different move: from the ground one only climbs, from orbit
    #: one crosses or comes down, under way one only turns back, adrift one
    #: lays a new course from wherever inertia has taken the hull, and lost
    #: one does nothing -- the hull and its crew are gone. Not
    #: derivable by the client -- `docked` is a key, and whether that key
    #: names an orbit is a fact about the world (D-225).
    flying = await _flight(session, ship)
    if ship.lost_at is not None:
        stage = LOST
    elif docked is not None:
        stage = IN_ORBIT if is_orbit(docked) else AT_PORT
    elif flying is not None:
        stage = UNDER_WAY
    else:
        stage = ADRIFT

    def priced_sample(sample: sky.Sample | None) -> dict[str, object] | None:
        """One point of the slider, priced: what it takes and what it burns (D-271, D-289).

        The passage pays for delta-v at both ends. What must be in the tanks
        besides is the descent at the far end -- one number per planet, sent
        beside the arcs as `reserve` rather than added into each of them
        (D-225).
        """
        if sample is None or have_class is None:
            return None
        burn = course.fuel_for_speed(
            constants, weight, sample.dv, efficiency=efficiency(constants, have_class)
        )
        return {
            "hours": round(sample.hours, ROUND_HOURS),
            "dv": round(sample.dv, ROUND_DV),
            "fuel": round(burn, ROUND_MASS),
        }

    def priced(hours: float | None, *, reserve: float = 0.0) -> dict[str, object]:
        """One offered move, priced: what it takes, what it burns, what it needs.

        `fuel` is spent now; `needs` is what must be in the tanks before the
        order is taken at all, because a leg that ends where there is no bunker
        is refused without the fuel to leave again (pillar P6, `flight._burn`).
        The two are equal wherever the leg ends on the ground.
        """
        return {
            "hours": None if hours is None else round(hours, ROUND_HOURS),
            "fuel": (
                None
                if hours is None
                else round(fuel_for(constants, weight, hours, klass=have_class), ROUND_MASS)
            ),
            "needs": (
                None
                if hours is None
                else round(
                    fuel_for(constants, weight, hours + reserve, klass=have_class), ROUND_MASS
                )
            ),
            #: Reachable or not is about **thrust**, and nothing else: a ship
            #: that cannot leave the ground cannot leave it for any destination.
            #: Class closes no route (D-235); fuel is the player's arithmetic,
            #: and `needs` is there for them to do it with.
            "reachable": (
                hours is not None
                and have_class is not None
                and thrust_ratio >= constants[R.SHIP_MIN_THRUST_RATIO]
            ),
        }

    #: The climb, offered while the hull stands on the ground and priced by the
    #: planet's own gravity (D-245). One destination, always the same one: the
    #: orbit above the pad. It keeps back the descent that would bring the hull
    #: home, which is why `needs` is the larger of the two numbers here.
    #:
    #: Offered with no engine aboard as well, priced at nothing and marked
    #: unreachable: "не отрывается" and "у планеты нет орбиты" are two different
    #: sentences, and a `climb` dropped to nothing said the second where the
    #: first was true.
    up = None
    if stage is AT_PORT and docked is not None:
        orbit = await orbit_node_of(session, docked.planet)
        if orbit is not None:
            up = {
                "node": orbit.key,
                "name": orbit.name,
                "planet": orbit.planet.value,
                **priced(
                    climb_hours(constants, docked.planet, thrust_ratio)
                    if thrust_ratio > 0
                    else None,
                    reserve=(
                        fall_hours(constants, docked.planet, thrust_ratio)
                        if thrust_ratio > 0
                        else 0.0
                    ),
                ),
            }

    #: The crossings, offered from orbit and from nowhere else. One row per
    #: **planet**, not per port: between worlds one goes orbit to orbit, and
    #: which pad the hull ends on is chosen later, over the planet it picked.
    #: The sky is asked once per planet for the same reason -- a planet with
    #: hundreds of spaceports (Aurora, D-230) has one distance, not hundreds.
    #:
    #: Only the planets one may actually come down on: a world whose beacons
    #: have all gone out is one a hull reaches and never leaves the orbit of
    #: (D-232), and `flight.fly` refuses it. The console does not offer what the
    #: engine will refuse.
    routes: list[dict] = []
    landings: list[dict] = []
    down = None
    if stage in (IN_ORBIT, ADRIFT):
        open_planets = await _open_planets(session)
        #: **Once.** `lit_ports` walks every landing in the world and asks the
        #: frozen ones about warmth node by node; the ground console answers for
        #: a whole fleet at once (D-242), so a second call here was that walk
        #: again, per hull.
        lit = await lit_ports(session, constants)
        #: A planet one lands anywhere on is named in the list by **its own**
        #: name, not by the node the row happens to carry: the hull comes down
        #: where the roll puts it (D-235), and a row promising "Плато
        #: Наковальни" would be a promise the landing does not keep.
        spheres = {
            node.planet: node.name
            for node in (
                await session.execute(
                    select(Node).where(Node.key.in_(sorted(one.value for one in open_planets)))
                )
            )
            .scalars()
            .all()
        }
        reachable = {port.planet for port in lit}
        orbits = {
            node.planet: node
            for node in (
                await session.execute(
                    select(Node).where(Node.key.in_(sorted(orbit_key(one) for one in reachable)))
                )
            )
            .scalars()
            .all()
        }
        bodies = await sim.system(session, constants)
        for target in sorted(reachable, key=lambda one: one.value):
            orbit = orbits.get(target)
            if orbit is None or (docked is not None and target is docked.planet):
                continue
            #: From where the hull **is** (D-289): the parking circle it sits
            #: on, or the point inertia has carried it to. The whole slider is
            #: `forecast`, read when the planet is chosen -- forty samples per
            #: world in every summary would be the redundancy D-225 names.
            samples = await sim.offers(
                session,
                constants,
                catalog,
                ship,
                bodies.body(target.value),
                now=moment,
                thrust_ratio=thrust_ratio,
            )
            if not samples:
                continue
            kept = (
                fuel_for(
                    constants, weight, fall_hours(constants, target, thrust_ratio), klass=have_class
                )
                if thrust_ratio > 0 and have_class is not None
                else 0.0
            )
            cheap = min(samples, key=lambda one: one.dv)
            fast = next(
                (
                    one
                    for one in samples
                    if thrust_ratio > 0
                    and one.dv <= course.deliverable(constants, thrust_ratio, one.hours)
                ),
                None,
            )
            routes.append(
                {
                    "node": orbit.key,
                    "name": orbit.name,
                    "planet": target.value,
                    "cheap": priced_sample(cheap),
                    "fast": priced_sample(fast),
                    #: The descent at the far end: the console's warning, not
                    #: the engine's refusal since D-289 -- what an arc needs
                    #: is its own fuel plus this to come down at the end.
                    "reserve": round(kept, ROUND_MASS),
                    #: Reachable or not is about **thrust**, and nothing else:
                    #: a ship that cannot leave the ground cannot leave it for
                    #: any destination. Class closes no route (D-235).
                    "reachable": (
                        have_class is not None
                        and thrust_ratio >= constants[R.SHIP_MIN_THRUST_RATIO]
                    ),
                }
            )
    if stage is IN_ORBIT and docked is not None:
        #: The pads under the hull. Every lit one of them, because this is the
        #: moment the choice is actually made (D-245) -- and a planet one lands
        #: **anywhere** on is one row rather than one per field (D-233): its
        #: fields differ in nothing the console could show, and their number
        #: grows with every scout. The node the hull comes down in is rolled at
        #: the landing, so the row is named after the planet and not after
        #: whichever field it happens to carry.
        #: The price of coming down is a fact about the **planet**, not about
        #: the pad: hours, fuel and reach are the same for every field of it.
        #: Sent once, beside the list, because Aurora has hundreds of piers
        #: (D-230) and a copy of the same five numbers in each of them is
        #: exactly the redundancy D-225 exists against.
        down = priced(
            fall_hours(constants, docked.planet, thrust_ratio) if thrust_ratio > 0 else None
        )
        named = False
        for port in sorted(lit, key=lambda one: one.key):
            if port.planet is not docked.planet:
                continue
            if port.planet in open_planets:
                if named:
                    continue
                named = True
            landings.append(
                {
                    "node": port.key,
                    "name": (
                        spheres.get(port.planet, port.name)
                        if port.planet in open_planets
                        else port.name
                    ),
                    **({"anywhere": True} if port.planet in open_planets else {}),
                }
            )

    return {
        "ship": str(ship.id),
        "name": ship.name,
        "nodes": len(nodes),
        "mass": round(weight, ROUND_MASS),
        #: Where the mass comes from and what pushes it (D-230): the console
        #: shows what to cut and what to add, not just the two totals.
        "mass_parts": {
            part: round(value, ROUND_MASS)
            for part, value in (
                await mass_parts(session, constants, catalog, ship, things=things)
            ).items()
        },
        "engines": await engines(session, constants, ship, things=things),
        "thrust": round(pull, ROUND_MASS),
        "ratio": round(thrust_ratio, ROUND_RATIO),
        "min_ratio": constants[R.SHIP_MIN_THRUST_RATIO],
        "class": have_class,
        "crew": crew,
        #: Whether a system stands aboard at all (D-288): not how many people
        #: it holds -- nothing holds a number of people any more, the air on
        #: its line does, and that is `air` below.
        "life_support": await life_support(session, ship, things=things) > 0,
        "fuel": round(
            await fuel_aboard(session, constants, catalog, ship, things=things), ROUND_MASS
        ),
        #: The air (D-233, D-234). On the console rather than in `look`, because
        #: it is a fact about the **hull** and not about the room one stands in:
        #: the whole ship shares one atmosphere, and every compartment of it
        #: reads the same number. The hold is handed over rather than read
        #: again: `oxygen` would otherwise walk the same rooms a third time.
        "air": await _oxygen().gauge(session, constants, catalog, ship, crew=crew, things=things),
        #: The grid the console's floor plan snaps to (D-240). An execution
        #: number of the server's, and the client cannot derive it: a copy of it
        #: in the client would silently skew every hull the day it changes.
        "grid": {"cell": SHIP_GRID, "reach": SHIP_GRID_REACH},
        #: Which planet the hull is at. The console's chart draws it there, and
        #: it cannot be derived from `docked`: a ship that has cast off has no
        #: port at all and still stands in somebody's sky (D-225).
        "planet": planet.value,
        #: Which of the three stages of a journey the hull is at (D-245): on the
        #: ground, in orbit, or under way. The whole console hangs on it -- the
        #: buttons offered, the chart's own drawing of the hull, the wording of
        #: every refusal -- and no other key says it.
        "stage": stage,
        #: The climb to the orbit above, while there is one to make. `None` in
        #: orbit and under way: there is no such move from there.
        "climb": up,
        #: What coming down costs from here -- one price for the whole planet
        #: (D-245). `None` anywhere but in orbit.
        "descent": down,
        #: Which pads it may come down on: names only, because the price above
        #: is the same for all of them. Chosen with the planet already below,
        #: which is when a crew knows what it is choosing between.
        "landings": landings,
        "docked": None if docked is None else docked.key,
        "port": None if docked is None else docked.name,
        #: Whether the hull has a console of its own. It is the **receiver**: a
        #: ground console talks to it, and a hull without one takes no order at
        #: all, its crew's or anybody's (D-242). The client cannot derive it --
        #: what stands aboard is not in this answer (D-225).
        #:
        #: Off the hold already read: asking room by room was a query apiece,
        #: and `ship.view` answers for a whole fleet at once.
        "bridge": any(thing.type_key in consoles and thing.installed for thing in things),
        #: The pier it cast off from, if it has ever cast off: what a turn-back
        #: aims at, and what the button names. The name alone -- the key would
        #: be a second way to say the same thing, and `ship.recall` takes no
        #: destination (D-225).
        "left": None if home is None else home.name,
        #: The passage under way, if there is one (D-240). The console draws the
        #: hull on its own chart and must say where it is going: a ship in
        #: flight has no edges at all, so nothing else in the answer could tell.
        "flight": flying,
        #: The hull in the sky (D-289): where it is and where inertia takes it
        #: -- the chart draws the coast and the console names its end. Nothing
        #: at a spaceport, on a leg, or on the circle: that one the chart
        #: draws by itself.
        "sky": await sim.picture(session, constants, catalog, ship, now=moment),
        #: Who else is in the sky near this hull (D-289, wave 3): one's own
        #: hulls always, foreign ones within the sight radius or moored at
        #: the same planet. A drifter among them may be aimed at, and the
        #: chart offers it the way it offers a planet.
        "sightings": await sighting.sightings(session, constants, ship, now=moment),
        #: The hold and the docking: the hull this one flies as one with, the
        #: hull it is joined to, and whose consent is still wanting.
        **await sighting.ties(session, ship),
        #: What speed the tanks buy at this mass, units a day: the console
        #: reads the plan's delta-v against it, and warns before the button rather
        #: than refusing after it (D-289).
        "dv": round(
            sim.dv_aboard(
                constants,
                await fuel_worth(session, constants, catalog, ship),
                weight,
                have_class,
            ),
            ROUND_DV,
        ),
        #: The order under way, in two numbers (D-289): the plan's delta-v, and
        #: what is left of it to burn -- the console reads the second against
        #: the tanks. The line itself is `flight.arc`; the rest of the order
        #: is the helm's business and stays off the wire (D-225).
        "course": (
            None
            if not ship.course
            else {
                "dv": ship.course.get("dv"),
                "left": round(
                    max(
                        float(ship.course.get("dv") or 0.0)
                        - float(ship.course.get("spent") or 0.0),
                        0.0,
                    ),
                    ROUND_DV,
                ),
            }
        ),
        #: Which berth of that port, and therefore how long the gangway is: a
        #: busy yard boards you further from the door (D-201).
        "berth": ship.berth,
        "connector": None if connector is None else connector.key,
        #: The crossings between worlds, offered from orbit only: one row per
        #: planet, each aimed at that planet's orbital node.
        "routes": sorted(routes, key=lambda route: (not route["reachable"], route["name"])),
    }


def _nothing(target: Ship, refused: ShipError) -> dict[str, object]:
    """An empty slider to a hull, with the refusal the order would meet: the
    key and its arguments as the socket quotes them, for the console to say
    in the reader's language (D-225: nothing the client could derive)."""
    return {
        "planet": None,
        "ship": str(target.id),
        "reserve": 0.0,
        "samples": [],
        "why": {"code": refused.key, "args": refused.params},
    }


async def forecast(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    target: Planet | Ship,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """The slider (D-271, D-289): every arc to `target` the sky offers from
    where the hull is right now, priced for this hull.

    Samples from the hull's own state -- the parking circle or the point
    inertia has carried it to -- with what each burns by its class and mass,
    whether the engines can give that delta-v in that time (`ok`), and the
    two-body arc to draw while the slider moves. The client draws the range
    between the fastest `ok` sample and the cheapest one and sends back the
    hours it picked; the casting off flies that one under the whole sky.
    Empty for a hull not in the sky: from a pad one only climbs.
    """
    moment = now or datetime.now(UTC)
    weight = await mass(session, constants, catalog, ship)
    thrust_ratio = await ratio(session, constants, catalog, ship)
    have_class = await engine_class(session, constants, ship)
    bodies = await sim.system(session, constants)
    goal: sky.Target | None
    if isinstance(target, Ship):
        #: A hull as the target (wave 3): only one in sight and coasting, with
        #: a forecast to be met on -- else nothing to offer, and the reason in
        #: the engine's own words (`why`): an empty slider blamed the engines
        #: before, and a hull that will be gone by the hour is not their fault.
        try:
            await sighting.aimable(session, constants, ship, target, now=moment)
        except ShipError as refused:
            return _nothing(target, refused)
        goal = await sim.drifter_of(session, constants, target)
        if goal is None:
            return _nothing(target, NoArc(key="ship-target-unknown"))
    else:
        goal = bodies.body(target.value)
    offered = await sim.offers(
        session, constants, catalog, ship, goal, now=moment, thrust_ratio=thrust_ratio
    )
    t0 = await sky_days(session, moment)
    if isinstance(goal, sky.Drifter) and any(sim.gone_by(goal, t0, one.hours) for one in offered):
        #: The hull's line ends before the profile gets there: nothing is
        #: offered, as nothing would be flown (`sim.depart` refuses it).
        assert isinstance(target, Ship)
        return _nothing(target, NoArc(key="ship-target-gone-by-then", other=target.name))
    share = 1.0 if have_class is None else efficiency(constants, have_class)
    #: The descent at the far end -- a planet's; a hull has no ground to come
    #: down onto, and nothing is kept for it.
    reserve = (
        fuel_for(constants, weight, fall_hours(constants, target, thrust_ratio), klass=have_class)
        if thrust_ratio > 0 and isinstance(target, Planet)
        else 0.0
    )
    samples = []
    for sample in offered:
        burn = course.fuel_for_speed(constants, weight, sample.dv, efficiency=share)
        samples.append(
            {
                "hours": round(sample.hours, ROUND_HOURS),
                "dv": round(sample.dv, ROUND_DV),
                "fuel": round(burn, ROUND_MASS),
                #: An arc is the engines' to deliver or not; the approach
                #: profile to a hull is laid within the thrust by construction.
                "ok": not isinstance(target, Planet)
                or sample.dv <= course.deliverable(constants, thrust_ratio, sample.hours),
                #: The arc the chart draws for this point while the slider is
                #: held on it: the planner's two-body line, not the flown one
                #: (D-289) -- the flown line is settled at the order.
                "trace": [[round(x, ROUND_TRACE), round(y, ROUND_TRACE)] for x, y in sample.trace],
            }
        )
    #: The descent kept back at the far end, once: every sample needs its own
    #: fuel plus this, and the client adds the two (D-225).
    return {
        "planet": target.value if isinstance(target, Planet) else None,
        "ship": str(target.id) if isinstance(target, Ship) else None,
        "reserve": round(reserve, ROUND_MASS),
        "samples": samples,
    }
