# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The sky as the console reads it: the passages a hull can make, the lit
beacons and ports, where a landing is allowed and which planets open.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.db.base import remember
from src.engine import world
from src.engine.ship._base import (
    OPEN_LANDING,
    SPACEPORT,
)
from src.engine.ship.belonging import is_aboard
from src.models.inventory import Container, ContainerKind, Item
from src.models.job import Job, JobKind, JobState
from src.models.ship import Ship
from src.models.world import Layer, Node, Planet


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
            #: The arc a crossing flies, in map units (D-271): the map draws
            #: the hull along it, at the share of the time gone. Legs to and
            #: from the ground carry none -- they are drawn beside the planet.
            "arc": job.payload.get("arc"),
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
                    Item.installed.is_(True),
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
        #: The planet the arc bends round, if any (D-271): the console names
        #: it. And the arc itself, for the chart to draw the hull along.
        "via": job.payload.get("via"),
        "arc": job.payload.get("arc"),
    }
