# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: what the client shows before the attempt.

Split out of `engine/ship.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.db.base import remember
from src.engine import places, travel, world
from src.engine.ship._base import (
    AT_PORT,
    BRIDGE,
    IN_ORBIT,
    OPEN_LANDING,
    SPACEPORT,
    UNDER_WAY,
    is_orbit,
    orbit_key,
    orbit_node_of,
)
from src.engine.ship.belonging import crew_of, is_aboard, nodes_of, of_node
from src.engine.ship.physics import (
    _things,
    base_hours,
    climb_hours,
    engine_class,
    engines,
    fall_hours,
    fuel_aboard,
    fuel_for,
    life_support,
    mass,
    mass_parts,
    passage_hours,
    thrust,
)
from src.models.inventory import Container, ContainerKind, Item
from src.models.job import Job, JobKind, JobState
from src.models.ship import Ship
from src.models.world import Edge, Layer, Node, Planet
from src.runtime import SHIP_GRID, SHIP_GRID_REACH
from src.units import (
    ROUND_HOURS,
    ROUND_MASS,
    ROUND_RATIO,
)


def _oxygen():
    """The oxygen module, imported late.

    `oxygen` reads a hull through this very package, so the import cannot stand
    at the top: this is the one edge of that cycle, named where it is broken.
    """
    from src.engine import oxygen  # noqa: PLC0415 -- lazy: breaks the cycle with oxygen

    return oxygen


async def in_sight(
    session: AsyncSession, constants: Constants, node: Node
) -> dict[str, list[dict[str, object]]] | None:
    """What of ships is visible from this node, and nothing beyond it.

    A ship's interior is **not on the public map** (D-201). From the pier a ship
    is one hull, and how many cabins it holds, what is joined to what and where
    the hold is stays unknown -- that is the whole point of the single
    connector: nothing is seen past the gangway. So what a ship shows travels
    with the look of whoever stands close enough, and only what they may see:

    * **at a spaceport** -- the ships moored to it, each as one node with its
      gangway. That is the door, not the inside: it appears on walking up to the
      pier and is gone on walking away from it;
    * **aboard** -- the rooms and the ways between them, because from inside a
      ship is an ordinary piece of the graph one walks around.

    None means neither: ordinary ground with no ship within sight.
    """
    if is_aboard(node):
        return await _from_aboard(session, constants, node)
    return await _from_pier(session, constants, node)


async def _from_pier(
    session: AsyncSession, constants: Constants, port: Node
) -> dict[str, list[dict[str, object]]] | None:
    """Ships moored here: a door apiece, on the layer the pier itself is on.

    A moored ship stands in the city as a building does -- one walks up to it
    and up its gangway -- so that is where the map shows it, under the same city
    as the port. Its own layer stays what it is; this is the delegate's trick
    the map has used from the start (D-045), not a second kind of node.
    """
    moored = (
        (await session.execute(select(Ship).where(Ship.docked_node_id == port.id))).scalars().all()
    )
    if not moored:
        return None

    city = None if port.parent_id is None else await session.get(Node, port.parent_id)
    nodes: list[dict[str, object]] = []
    edges: list[dict[str, object]] = []
    for ship in moored:
        connector = await session.get(Node, ship.connector_node_id)
        if connector is None:  # pragma: no cover -- a ship always has one
            continue
        nodes.append(
            {
                "key": connector.key,
                #: The ship's name, not the compartment's: from the pier one
                #: sees «Заря», and what its first room is called is a thing
                #: learnt aboard.
                "name": ship.name,
                "layer": port.layer.value,
                "parent": None if city is None else city.key,
                "exit": False,
                "port": False,
                "planet": connector.planet.value,
                "orbit": None,
                "deferred": False,
                "aboard": True,
                "flight": None,
            }
        )
        gangway = await travel._edge_between(session, port.id, connector.id)
        if gangway is not None:
            edges.append(
                {
                    "a": port.key,
                    "b": connector.key,
                    "surface": gangway.surface.value,
                    "seconds": round(travel.edge_seconds(constants, gangway)),
                }
            )
    return {"nodes": nodes, "edges": edges} if nodes else None


async def _from_aboard(
    session: AsyncSession, constants: Constants, node: Node
) -> dict[str, list[dict[str, object]]] | None:
    """The ship one is standing in: its rooms and the ways between them."""
    ship = await of_node(session, node)
    if ship is None:  # pragma: no cover -- an aboard node always has its ship
        return None

    rooms = await nodes_of(session, ship)
    delegate = await session.get(Node, ship.node_id)
    keys = {room.id: room.key for room in rooms}
    if delegate is not None:
        keys[delegate.id] = delegate.key
    #: The gangway too, when there is one: from inside the way out is a fact of
    #: the graph like any other, and without it the interior hangs on nothing.
    port = None if ship.docked_node_id is None else await session.get(Node, ship.docked_node_id)
    if port is not None:
        keys[port.id] = port.key

    ways = (
        (
            await session.execute(
                select(Edge).where(or_(Edge.node_a_id.in_(keys), Edge.node_b_id.in_(keys)))
            )
        )
        .scalars()
        .all()
    )
    return {
        "nodes": [
            {
                "key": room.key,
                "name": room.name,
                "layer": room.layer.value,
                "parent": None if delegate is None else delegate.key,
                #: Where the room stands on the ship's **own** map (D-237,
                #: D-240). Sent only from aboard: from the pier a hull is one
                #: node, and these coordinates are the interior's -- drawing a
                #: moored ship by them would put its cabins across the city.
                #: With this the client stops settling a hull with springs, and
                #: the arrangement its owner made is the one everybody aboard
                #: sees.
                "place": places.wire(room),
                "ring": None,
                "exit": False,
                "port": False,
                "planet": room.planet.value,
                "orbit": None,
                "deferred": False,
                "aboard": True,
                "flight": None,
            }
            for room in rooms
        ],
        "edges": [
            {
                "a": keys[edge.node_a_id],
                "b": keys[edge.node_b_id],
                "surface": edge.surface.value,
                "seconds": round(travel.edge_seconds(constants, edge)),
            }
            for edge in ways
            if edge.node_a_id in keys and edge.node_b_id in keys
        ],
    }


async def passages(session: AsyncSession) -> dict[uuid.UUID, dict[str, object]]:
    """Ships under way: the delegate node -> where it goes and when it arrives.

    A ship in flight is **nowhere in the graph**: undocking removes its only
    edge (D-201), so its place cannot be read off the map the way everything
    else can. It is known to one thing only -- the job that will bring the ship
    in -- and that job holds both ends of the passage: it was created at
    departure and fires on arrival. The share between the two is exactly how
    far the ship has got, and that is what the space layer draws.

    Keyed by the delegate node, because that is what the map speaks in.
    """
    flights = (
        (
            await session.execute(
                select(Job).where(Job.kind == JobKind.SHIP_FLIGHT, Job.state == JobState.PENDING)
            )
        )
        .scalars()
        .all()
    )
    if not flights:
        return {}

    afloat = {
        str(ship.id): ship
        for ship in (await session.execute(select(Ship).where(Ship.docked_node_id.is_(None))))
        .scalars()
        .all()
    }
    under_way: dict[uuid.UUID, dict[str, object]] = {}
    for job in flights:
        ship = afloat.get(str(job.payload.get("ship")))
        if ship is None:
            continue
        under_way[ship.node_id] = {
            "to": uuid.UUID(str(job.payload["to"])),
            "started_at": job.created_at,
            "arrives_at": job.run_at,
        }
    return under_way


async def beacon_lit(session: AsyncSession, constants: Constants, port: Node) -> bool:
    """Whether the port's beacon shines -- whether a ship may aim at it at all.

    A spaceport works while its node is warm and its yard has power (D-231,
    D-232). On a planet where nothing freezes the question does not arise: a
    port there is a port, and an unpaid bill is the meter's business, not the
    sky's.

    This is the rule the whole of Aurora hangs on. **Its blackout is
    irreversible**: let the last working spaceport of the planet go out and
    there is nowhere left to land -- the planet is lost, and the world does not
    insure anybody against it. A brazier is not enough to relight one: warmth
    it gives, power it does not, and power has to be walked in.
    """
    #: Lazy: `ship` is imported by `frost` (a node aboard is always warm), and
    #: `energy` comes along on the same line for the same reason.
    from src.engine import energy, frost  # noqa: PLC0415 -- lazy: breaks the cycle with frost

    if await frost.climate_of(session, port) != frost.FROST:
        return True
    #: Power first: it is one reading per **city**, remembered for the command,
    #: while warmth is three per node and asks about the neighbours. A dark city
    #: is the common case on a planet of dead cities, and it must cost one query.
    return await energy.powered(session, constants, port) and await frost.is_warm(
        session, constants, port
    )


async def lit_ports(session: AsyncSession, constants: Constants) -> list[Node]:
    """The ports a ship may actually reach: the ones whose beacon shines.

    Every city a scout finds beyond the ice comes with a pier of its own
    (`ruins.lost_city`), and every one of them is dark: the list of ports grows
    with play, and the dark ones must not cost a reading each. So the cheap half
    of the answer -- has this city any energy at all -- is asked for **all**
    cities in one query, and only the ports that pass it are asked the dear
    question about warmth.
    """
    from src.engine import energy, frost  # noqa: PLC0415 -- lazy: breaks the cycle with frost

    every = await landings(session)
    if not every:  # pragma: no cover -- there is always the capital's pier
        return []
    powered = await energy.cities_with_power(session, constants)
    found = []
    for port in every:
        #: Bare ground has no beacon to light: on a planet one lands anywhere
        #: on, every surface node is a landing site and always was (D-233).
        if await frost.climate_of(session, port) != frost.FROST:
            found.append(port)
            continue
        if port.parent_id not in powered:
            continue
        if await frost.is_warm(session, constants, port):
            found.append(port)
    return found


async def landings(session: AsyncSession) -> list[Node]:
    """Everywhere a ship may aim at: the yards, and the bare ground of planets
    that take a landing anywhere (D-233).

    A dark port is here too -- being dark makes a place no less a place; what it
    is not is a destination (`lit_ports`).
    """
    return [*await ports(session), *await open_landings(session)]


async def ports(session: AsyncSession) -> list[Node]:
    """Nodes with a **yard** in them, and only those.

    The map draws its piers off this list (`routes.public`), so bare ground a
    ship may set down on does not belong here: Pyroxis has no spaceport and
    cannot have one (D-233), and a legend calling every black field a spaceport
    would be a lie told six times over.
    """
    with_yard = (
        (
            await session.execute(
                select(Node)
                .join(Container, Container.owner_id == Node.id)
                .join(Item, Item.container_id == Container.id)
                .where(
                    Container.kind == ContainerKind.NODE,
                    Item.type_key.in_(world.station_names(SPACEPORT)),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    return list(with_yard)


async def open_landings(session: AsyncSession) -> list[Node]:
    """The surface nodes of planets one lands anywhere on (D-233).

    Every node of the surface, and the surface only: the planet's own node is
    where it stands in the sky, not a place to put a hull down on. A node
    aboard a ship is not ground either, however low the ship flies.

    A list, and it grows with the planet: every field a scout opens on Pyroxis
    is another destination. D-233 wants the console to answer with the
    **planet** instead, and the node to be chosen after the course is set --
    which is a gesture the client does not have yet. The split is here already
    (`ports` is yards and nothing else, so the map's piers are not lied to);
    what is left is interface work, and it is named in the roadmap as such.
    """
    planets = await _open_planets(session)
    if not planets:
        return []
    found = (
        (
            await session.execute(
                select(Node).where(
                    Node.planet.in_([planet.value for planet in planets]),
                    Node.layer != Layer.SPACE,
                )
            )
        )
        .scalars()
        .all()
    )
    return [node for node in found if not is_aboard(node)]


async def lands_anywhere(session: AsyncSession, node: Node) -> bool:
    """Whether a ship may set down **on this node**.

    The planet's property and the node's own nature, both: the sphere of a
    planet is where it stands in the sky, not ground to put a hull on, and a
    room aboard another ship is not ground either. `open_landings` says the
    same thing about the whole planet at once, and the two must not disagree --
    a flight is refused by one and offered by the other.
    """
    if node.layer is Layer.SPACE or is_aboard(node):
        return False
    return node.planet in await _open_planets(session)


async def _open_planets(session: AsyncSession) -> frozenset[Planet]:
    """Which planets are landed on without a port. Four rows, one reading."""

    async def read() -> frozenset[Planet]:
        spheres = (
            (
                await session.execute(
                    select(Node).where(Node.key.in_([planet.value for planet in Planet]))
                )
            )
            .scalars()
            .all()
        )
        return frozenset(
            sphere.planet for sphere in spheres if (sphere.properties or {}).get(OPEN_LANDING)
        )

    return await remember(session, ("open_landing_planets",), read)


async def _flight(session: AsyncSession, ship: Ship) -> dict[str, object] | None:
    """Where this hull is bound and when it is due, or nothing if it is not flying.

    Read off the job that carries it: a passage lives in that job and nowhere
    else, and a second place to keep it would be a second opinion about where
    the ship is. Asked about **this** hull rather than through `passages` --
    that one gathers every flight in the world, and the console asks about one.
    """
    #: Lazy: `flight` reads the beacon and the landings from this module.
    from src.engine.ship.flight import _passage_of  # noqa: PLC0415 -- lazy: breaks the cycle

    job = await _passage_of(session, ship)
    if job is None:
        return None
    goal = await session.get(Node, uuid.UUID(str(job.payload["to"])))
    return {
        "to": None if goal is None else goal.key,
        "name": None if goal is None else goal.name,
        "planet": None if goal is None else goal.planet.value,
        "started_at": job.created_at.isoformat(),
        "arrives_at": job.run_at.isoformat(),
        #: Whether this is the way back (D-242). A turn-back is not turned back
        #: again, and without this the console kept the button lit and collected
        #: a refusal per click. Not derivable: the destination alone does not
        #: say which way the helm went.
        "back": bool(job.payload.get("back")),
    }


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
    #: Where the hull is in its journey (D-245). Three stages, and each offers a
    #: different move: from the ground one only climbs, from orbit one crosses
    #: or comes down, and under way one only turns back. Not derivable by the
    #: client -- `docked` is a key, and whether that key names an orbit is a
    #: fact about the world (D-225).
    stage = UNDER_WAY if docked is None else (IN_ORBIT if is_orbit(docked) else AT_PORT)

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
    if stage is IN_ORBIT and docked is not None:
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
        for target in sorted(reachable, key=lambda one: one.value):
            orbit = orbits.get(target)
            if orbit is None or target is docked.planet:
                continue
            table = await base_hours(session, constants, planet, target, at=moment)
            if table is None:
                continue
            routes.append(
                {
                    "node": orbit.key,
                    "name": orbit.name,
                    "planet": target.value,
                    **priced(
                        passage_hours(constants, table, thrust_ratio) if thrust_ratio > 0 else None,
                        reserve=fall_hours(constants, target, thrust_ratio),
                    ),
                }
            )

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
        "life_support": await life_support(session, constants, ship, things=things),
        "fuel": round(await fuel_aboard(session, ship), ROUND_MASS),
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
        "bridge": any(thing.type_key in consoles for thing in things),
        #: The pier it cast off from, if it has ever cast off: what a turn-back
        #: aims at, and what the button names. The name alone -- the key would
        #: be a second way to say the same thing, and `ship.recall` takes no
        #: destination (D-225).
        "left": None if home is None else home.name,
        #: The passage under way, if there is one (D-240). The console draws the
        #: hull on its own chart and must say where it is going: a ship in
        #: flight has no edges at all, so nothing else in the answer could tell.
        "flight": await _flight(session, ship),
        #: Which berth of that port, and therefore how long the gangway is: a
        #: busy yard boards you further from the door (D-201).
        "berth": ship.berth,
        "connector": None if connector is None else connector.key,
        #: The crossings between worlds, offered from orbit only: one row per
        #: planet, each aimed at that planet's orbital node.
        "routes": sorted(routes, key=lambda route: (not route["reachable"], route["name"])),
    }
