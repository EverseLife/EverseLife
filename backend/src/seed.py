# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The alpha's starting world: Terra's capital as it greets the first player.

Run from `backend/`: `python -m src.seed`. Running again breaks nothing -- the
world is created once and lives its own life from then on.

**This is a development scenario, not part of the game.** Everything it puts
into hands goes through `world.grant_item` with an explicit ground: matter does
not appear in the world anonymously (pillar P1), and such an arrival must be
visible in telemetry.

## The layout is data, this file is rules (D-243)

Which nodes the capital consists of, what edges join them and for how many
seconds, what machines stand where, what lies in whose pocket -- all of that
is the vault's (`data/world.yaml`, edited by the vault editor's «Мир» tab or
by hand) and reaches the engine as `build/world.json`. `src/seed_world.py`
interprets it: lays missing nodes, ensures machines, and assembles every one
of them by recipe (D-216) -- a recipe without a composition, an input nobody
makes, a circle in the ladder each stop the world from being created, loudly.

What remains here is what data cannot be: the planets and their orbits, the
city as an institution (D-154), the founders and their credentials (D-187),
the one-off repairs old worlds catch up by, and the two honest assumptions of
development time:

* **money is given to the city, not the player** -- there is no bank or credit
  before E4, and without money in the treasury there is nothing to pay the
  settlement grant from. Issue goes through the `genesis` account, i.e. it is
  visible in the invariant check. Players print with zero and get the grant by
  the city's decision (D-153);
* **coal and refined metal are given to the founders** (in the scenario's
  `pockets`) -- the mine is a twenty-minute walk, and one wants to see
  smelting and minting today.

## The shape of the world

A city is not a place but **a group of locations connected by short edges**
(D-045, D-089). A step inside the city is seconds (`travel.city_step`),
leaving the walls is `distance 1`, i.e. `travel.frontier_step` by **road**,
not offroad. Beyond that every ring of distance is pricier than the previous
(D-180) -- that is all the geography: going for a machine is cheap, going for
coal is a trip, reaching the frontier means fitting out an expedition.

The capital is created as an **institutional city** (D-154): it has a charter,
code-laws and a treasury, and the first player becomes its president.
Everything the authority changes afterwards it changes itself -- the seed only
sets the initial position.
"""

from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import seed_world
from src.constants import bootstrap, current, current_catalog
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.db.base import dispose, session_factory
from src.engine import account as accounts
from src.engine import (
    breed,
    death,
    energy,
    estate,
    frost,
    ledger,
    library,
    market,
    places,
    ruins,
    ship,
    tick,
    travel,
    utility,
    world,
)
from src.engine import city as town
from src.models.city import City
from src.models.estate import Building, Deed
from src.models.identity import Account, Identity
from src.models.inventory import Container, ContainerKind, Item
from src.models.ledger import AccountKind, PostingReason
from src.models.ship import Ship
from src.models.world import Edge, Layer, Node, Planet, Surface
from src.seed_surfaces import surfaces
from src.settings import settings
from src.units import PERCENT, money

log = logging.getLogger("everselife.seed")

CORE = "terra.capital.core"
#: The capital's spaceport: the city's second door (D-206). A node, because
#: ship groups couple to a node -- and the `Космическая верфь` machine in it is what
#: makes the node a port (D-176).
PORT = "terra.capital.port"
#: A species, not "ore in general" (D-151): what Hyom loads onto the terminal.
IRON = "Железная руда"

#: Money goes **into the capital's treasury**, not the player's pocket (D-153).
#: The player prints with zero and gets the settlement grant by the city's
#: decision -- i.e. by mechanic, not by script.
CITY_TREASURY_START = 5_000
#: The settlement grant the capital decided to pay. An authority decision
#: written by the seed for lack of a live president in the world's first second.
NEWCOMER_GRANT = "120"
#: Email and password of the starting identities are development test data
#: (D-187): developers log into the alpha with them. In production passwords
#: are changed from the account panel.
FOUNDERS = {
    "Тэрн": {
        "email": "tern@everse.life",
        "password": "tern-terra-2026",
        "surname": "Первопечатный",
        "age": 34,
        "about": "Шахтёр и основатель столицы: первый, кого напечатала машина.",
    },
    "Хём": {
        "email": "hem@everse.life",
        "password": "hem-terra-2026",
        "surname": "Торговый",
        "age": 29,
        "about": "Торговец у терминала: первый стакан столицы — его железо.",
    },
}


class Orbit(NamedTuple):
    """A planet's place in the picture: where it stands and how fast it goes round."""

    key: str
    name: str
    #: The same name in the genitive: the orbital node is named after the
    #: planet it hangs over (D-245), and Russian declines. Written out rather
    #: than derived -- four words against a rule that would be wrong on the
    #: fifth planet somebody adds.
    genitive: str
    planet: Planet
    #: Display radius in the map's own units. Orbits are **not to scale**
    #: (10-world/06): the proportions repeat the system's figure from the
    #: landing, so the client and the poster show one and the same sky.
    radius: float
    #: A full circle, in real days. This is not astronomy: the number decides
    #: how fast the sky turns. Terra's month is the measure -- some twelve
    #: degrees a day, so between two evenings the map looks different while
    #: inside one sitting it stands still.
    #:
    #: **The spread matters more than any single value.** What a player waits
    #: for is not a lap but a **conjunction**, and two planets meet every
    #: `Ta*Tb / |Ta-Tb|` days -- so periods lying close together mean meetings
    #: once a season, however briskly each planet runs. Hence the inner planets
    #: are fast and the outer ones slow, the way a real system is arranged.
    #:
    #: Two floors bound the spread from below. A pair can never meet more often
    #: than the **inner** planet's own year, whatever the outer one does. And a
    #: passage must stay a small share of the target's year, or aiming at a
    #: planet turns into chasing it, and half a day of delay costs a fivefold
    #: flight. Pyroxis is the tight one: it is the fastest, and every passage to
    #: it is measured against its short year.
    period_days: float
    #: Where the planet stood at the world's epoch, radians. Spread by hand:
    #: a system that starts in a line looks like a bug.
    phase: float
    #: Drawn, but not playable yet (D-104).
    deferred: bool = False
    #: The planet's climate (D-231): «мерзлота», «пекло» -- or nothing, where
    #: the ground keeps a body alive by itself. A property of the world rather
    #: than a constant: what a planet is, is written in the world.
    climate: str | None = None


#: The system, from the star outwards. Aquatica is here **because** it is out
#: of the alpha: what cannot be reached is shown and marked, so that a player
#: sees from the first day where the road does not go yet (50-interface/05).
SYSTEM = (
    Orbit("pyroxis", "Пироксис", "Пироксиса", Planet.PYROXIS, 60, 11, 0.80, climate=frost.HEAT),
    Orbit("terra", "Терра", "Терры", Planet.TERRA, 136, 28, 2.10),
    Orbit("aquatica", "Акватика", "Акватики", Planet.AQUATICA, 172, 70, 4.00, deferred=True),
    Orbit("aurora", "Аврора", "Авроры", Planet.AURORA, 220, 130, 2.28, climate=frost.FROST),
)


async def _system(session: AsyncSession) -> Node:
    """The planets of the space layer. Returns Terra -- the alpha's home.

    A planet is an ordinary node of the same graph; the layer only decides from
    what height it is seen (D-045). What it has of its own is an **orbit**: on
    this layer a place is a function of time, so the distance between two
    planets -- and with it the length of the passage between them -- changes by
    itself, without anybody moving a node.

    Each planet also gets an **orbital node** (D-245): the place a ship stands
    at between the ground and the sky. A node rather than a state of the hull,
    because the vault has always described it as one -- docks, stations and the
    interception points piracy and convoys rest on all want somewhere to stand.
    Today it is bare, and one may only moor to it.

    Idempotent, and that is what makes it a catch-up too: an existing planet
    keeps everything it carries and only learns its orbit.
    """
    for circle in SYSTEM:
        marks: dict[str, object] = {
            world.ORBIT: {
                world.ORBIT_RADIUS: circle.radius,
                world.ORBIT_PERIOD: circle.period_days,
                world.ORBIT_PHASE: circle.phase,
            },
        }
        if circle.deferred:
            marks[world.DEFERRED] = True
        if circle.climate is not None:
            marks[circle.climate] = True
        node = (
            await session.execute(select(Node).where(Node.key == circle.key))
        ).scalar_one_or_none()
        if node is None:
            await world.create_node(
                session,
                circle.key,
                circle.name,
                area_m2=1,
                planet=circle.planet,
                layer=Layer.SPACE,
                properties=marks,
            )
        else:
            #: A whole new dict rather than a key set in place: SQLAlchemy sees
            #: an assignment and misses a mutation inside a JSON column.
            node.properties = {**(node.properties or {}), **marks}
    await session.flush()
    await _orbits(session)
    return (await session.execute(select(Node).where(Node.key == "terra"))).scalar_one()


async def _orbits(session: AsyncSession) -> None:
    """One orbital node per planet, hanging under the planet itself (D-245).

    A deferred planet gets none: an orbit is a destination, and a destination
    for a world that is not open yet would be a way into it.

    Area is the one number that means nothing here -- nothing is built in orbit
    yet -- and it is `ship.node_area` rather than a nought so that the day a
    dock is laid there, there is something to lay it on.
    """
    constants = current()
    for circle in SYSTEM:
        if circle.deferred:
            continue
        key = ship.orbit_key(circle.planet)
        if (
            await session.execute(select(Node.id).where(Node.key == key))
        ).scalar_one_or_none() is not None:
            continue
        sphere = (
            await session.execute(select(Node).where(Node.key == circle.key))
        ).scalar_one_or_none()
        if sphere is None:  # pragma: no cover -- the loop above has just laid it
            continue
        await world.create_node(
            session,
            key,
            f"Околопланетная орбита {circle.genitive}",
            area_m2=constants[R.SHIP_NODE_AREA],
            planet=circle.planet,
            layer=Layer.SPACE,
            parent=sphere,
            anchor=sphere,
            properties={ship.ORBIT_NODE: True},
        )
    await session.flush()


async def seed(session: AsyncSession) -> Node:
    """Create the starting world if it does not exist yet, otherwise bring it up to date."""
    existing = (await session.execute(select(Node).where(Node.key == CORE))).scalar_one_or_none()
    if existing is not None:
        log.info("the starting world already exists: %s", existing.key)
        await catch_up(session, existing)
        return existing

    constants = current()

    #: Layers are a display abstraction over one graph (D-045): Terra is seen
    #: from space, the capital from the planet, the built-up area in the city.
    #: One walks on the leaves.
    await _system(session)

    #: The layout itself -- nodes, edges, veins, machines, stocks -- is the
    #: vault's scenario (D-243), and a fresh world is simply the case where all
    #: of it is missing.
    scenario, applied = await seed_world.apply(session, constants)
    core = applied.nodes[CORE]

    #: The Forerunners' Printer: free and twelve hours (D-028). It is also the
    #: only door into the world that never closes, hence it stands in the core.
    #: A **relic** (D-232): found, never made, never taken down -- and it is the
    #: thing itself that prints for free, not the ground under it. Laid by
    #: rules rather than the scenario: there is exactly one in the world.
    await ruins.grant_relic(
        session, core, death.PRINTER, origin="наследие Предтеч: принтер столицы"
    )
    #: A genesis library holds the base set (D-068, D-209): the capital's shelf
    #: is what the Forerunners left -- today the whole catalog; a library a
    #: city builds starts empty and fills as people bring carriers.
    await _shelves(session, scenario, applied)

    #: The capital is an institutional city (D-154): a charter from vault
    #: defaults, a treasury and code-laws. Everything set here the authority
    #: changes itself later. The prison and the spaceport are city land like
    #: the gate (D-176, D-206): a state location is never a "free plot".
    city = None
    for delegate in applied.city_nodes(scenario):
        founded = await town.found(session, current_catalog(), delegate, delegate.name)
        city = city or founded
        for node in applied.descendants(scenario, delegate.key):
            if node.owner_identity_id is None:
                node.owner_city_id = founded.id
        await session.flush()
        await _treasury(session, founded)
        #: The city's first decision: pay newcomers a settlement grant. Written
        #: by the seed for lack of a live president in the world's first second
        #: -- from then on it is an ordinary code-law, changed from the
        #: administration (D-153).
        founded.laws = {"newcomer_grant": NEWCOMER_GRANT}
        await session.flush()
    if city is None:  # pragma: no cover -- a scenario without a city is a defect
        raise RuntimeError("сценарий мира не основал ни одного города")

    #: The city pool is created at once: a city has one by construction (D-071).
    await energy.ensure_pools(session, constants)

    tern, tern_body = await world.spawn(session, "Тэрн", core, **_acct("Тэрн"))
    #: The first player is the founder: authority in the city appears with the
    #: first person, not by a separate script (D-154).
    await town.install_founder(session, city, tern)
    pocket = await world.body_container(session, tern_body)
    await seed_world.outfit(session, pocket, scenario.pockets.get("Тэрн", ()))
    #: The newcomer's seed fund: seeds are an item separate from the harvest
    #: (D-057). The cultivar is a base one, nobody's: everyone starts from it,
    #: and then the farmer either selects or watches the fund degrade.
    for crop, qty in (("spelt", 300), ("turnip", 200)):
        cultivar = await breed.landrace(session, current_catalog(), crop)
        await breed.seed_lot(session, current_catalog(), pocket.id, cultivar, qty, PERCENT)

    marketplace = applied.nodes["terra.capital.market"]
    hyom, hyom_body = await world.spawn(session, "Хём", marketplace, **_acct("Хём"))
    hyom_pocket = await world.body_container(session, hyom_body)
    await seed_world.outfit(session, hyom_pocket, scenario.pockets.get("Хём", ()))
    #: So that there is something to look at in the book from the first minute.
    await market.load(session, constants, hyom_body, IRON, 30)
    await market.sell(
        session,
        constants,
        current_catalog(),
        hyom,
        marketplace,
        type_key=IRON,
        tier=market.tier_of(constants, 64),
        price=money(3),
        quantity=30,
    )

    #: The other planets' surfaces (D-230): a spaceport on Pyroxis, the ports
    #: of the abandoned city on Aurora. Laid before the buildings, so the yards
    #: there get theirs by the same rule as the capital's.
    await surfaces(session)

    #: Buildings of city nodes: a machine is placed in a building and takes area
    #: (D-106), and the seed must let the building stand before the machine.
    #: The city's built-up area counts as fully built.
    await _buildings(session)

    await tick.ensure_scheduled(session)
    #: The household meter ticks with the world clock: maintenance runs by
    #: time and without players (D-149).
    await utility.ensure_scheduled(session)
    log.info(
        "starting world created: Terra's capital with administration, mine, players Tern and Hyom"
    )
    return core


async def _original_printer(session: AsyncSession, core: Node) -> None:
    """Put the Forerunners' own printer into the capital's core, once.

    A world seeded before D-232 has an ordinary printer standing there: the seed
    used to build one by recipe, and free printing hung on a property of the
    node. `grant_relic` alone would not help -- it steps aside when a machine of
    the class already stands here, and by that rule the original could never
    replace the copy. So the copy goes: there is exactly one Forerunners'
    Printer in the world (D-028), and the core is where it stands.
    """
    book = current_catalog().recipes
    yard = await world.node_container(session, core)
    standing = (
        (
            await session.execute(
                select(Item).where(
                    Item.container_id == yard.id,
                    Item.type_key.in_(world.station_names(death.PRINTER)),
                )
            )
        )
        .scalars()
        .all()
    )
    if any(book.is_relic(thing.type_key) for thing in standing):
        return
    for copy in standing:
        log.info("ядро столицы: копия принтера уступает место оригиналу (D-028)")
        await session.delete(copy)
    await session.flush()
    await ruins.grant_relic(
        session, core, death.PRINTER, origin="наследие Предтеч: принтер столицы"
    )


async def catch_up(session: AsyncSession, core: Node) -> None:
    """Bring an already existing world up to today's layout.

    The world is eternal, there are no wipes (D-007): "recreate the database"
    is not an answer. Here goes what cannot be added by a migration because it
    is content, not schema. The layout itself catches up by the scenario
    (D-243): `seed_world.apply` lays whatever node, edge or machine the vault
    has gained since the world was seeded. What stays written out by hand are
    the one-off repairs: rules that changed shape after worlds already lived
    under the old ones.

    Every step is idempotent: running again doubles nothing.
    """
    constants = current()

    capital = await session.get(Node, core.parent_id)
    if capital is None:  # pragma: no cover -- a core without a city is a bug
        return

    #: The rest of the system: a world laid out before the space layer had
    #: Terra alone in the sky, and a lone dot is not a system. The other three
    #: planets arrive with their orbits, and Terra learns its own.
    await _system(session)

    #: Places on the map (D-237). A world laid out before the rule has none,
    #: and the client would go on settling it with springs -- turned differently
    #: for every player and after every find. Nobody who has a place moves.
    laid = await places.backfill(session)
    if laid:
        log.info("map places given to %s nodes", laid)

    #: Berths (D-201): a ship moored before the piers were numbered has a
    #: gangway of whatever length the old rule gave it. The number itself comes
    #: from the migration, in docking order; the walk is relaid here, because
    #: what a berth is worth in seconds is the vault's business.
    await _berths(session, constants)

    #: A step across a hull is one second (D-240). Hulls built before that rule
    #: have their corridors laid at the city's step, so a ship of ten
    #: compartments walked like a small town and its owner had no way to shorten
    #: it. Relaid here rather than by the migration, for the same reason the
    #: gangways are: what a step is worth in seconds is the vault's number.
    await _ship_steps(session, constants)

    #: Login by email and password (D-187): identities created before it get
    #: the seed's test accounts. Only those without an email yet -- anything
    #: set by hand or from the account panel the catch-up does not touch.
    await _accounts_catch_up(session)

    city = await town.by_node(session, capital.id)
    if city is None:
        city = await town.found(session, current_catalog(), capital, capital.name)
        city.laws = {"newcomer_grant": NEWCOMER_GRANT}
        await _treasury(session, city)
        log.info("city founded on the existing world: %s", city.name)

    #: The city's doors (D-206). A world laid out before that decision has
    #: cities without a gate, and until every one of them has it a road from
    #: beyond the walls has nowhere to be tied. Done first, because the layout
    #: below draws edges itself.
    await _gates_catch_up(session)

    #: The layout (D-243): whatever node, edge, machine or kept stock the
    #: scenario has gained since this world was laid arrives here, by the same
    #: interpreter a fresh world is laid by.
    scenario, applied = await seed_world.apply(session, constants)

    #: A city the scenario gained after this world was laid (D-243) is founded
    #: by the catch-up like everything else. The capital is standing by here,
    #: so this loop skips it.
    for delegate in applied.city_nodes(scenario):
        if await town.by_node(session, delegate.id) is not None:
            continue
        founded = await town.found(session, current_catalog(), delegate, delegate.name)
        founded.laws = {"newcomer_grant": NEWCOMER_GRANT}
        await _treasury(session, founded)
        for node in applied.descendants(scenario, delegate.key):
            if node.owner_city_id is None and node.owner_identity_id is None:
                node.owner_city_id = founded.id
        log.info("city founded by catch-up: %s", founded.name)
    await session.flush()

    #: And its gate, if the scenario did not give it one (D-206). Asked a
    #: second time on purpose: `_gates_catch_up` above ran before the layout,
    #: so a city founded a dozen lines ago would otherwise stand without a door
    #: until the next deploy -- and until it has one, a road from beyond its
    #: walls has nowhere to be tied.
    await _gates_catch_up(session)

    #: Civic land: the capital's built-up area belongs to the city, and from it
    #: the city collects taxes and on it spends energy (D-149). After the
    #: layout, so a node it just laid becomes city land the same minute.
    children = (
        (await session.execute(select(Node).where(Node.parent_id == capital.id))).scalars().all()
    )
    for node in children:
        if node.owner_city_id is None and node.owner_identity_id is None:
            node.owner_city_id = city.id
    await session.flush()

    #: Rights are split (D-155), and offices created before that have the old
    #: set: `dashboard`, `charter` and `land` are simply absent from it. The
    #: founder's powers are full by construction -- we add rather than rewrite.
    for office in await town.offices(session, city):
        if office.identity_id == city.founder_identity_id:
            office.powers = list(town.FOUNDER_POWERS)
    await session.flush()

    #: The president: the world's first player. Authority appears with the
    #: person, not by a separate script (D-154).
    if city.founder_identity_id is None:
        first = (
            (await session.execute(select(Identity).order_by(Identity.created_at)))
            .scalars()
            .first()
        )
        if first is not None:
            await town.install_founder(session, city, first)
    elif await town.citizenship(session, city.founder_identity_id) is None:
        #: A founder from before D-195 was a stranger in their own city: no
        #: vote, a newcomer's rate at the bank. Citizenship comes to them now.
        founder = await session.get(Identity, city.founder_identity_id)
        if founder is not None:
            await town._enrol_founder(session, city, founder)
            log.info("основателю выдано гражданство догоном: %s", founder.name)

    #: The Forerunners' Printer and the city printer: without them death would
    #: be a one-way ticket, and the world exists longer than the print mechanic (D-028).
    props = dict(core.properties or {})
    if not props.get(death.PRECURSOR):
        props[death.PRECURSOR] = True
        core.properties = props
    #: The **original** (D-028): eternal, free, unlimited, and there will never
    #: be a second. A relic, therefore: found, not made, and not to be taken
    #: down (D-232). What prints for free is the machine, not the ground it
    #: stands on -- a mark on a node would make a free printer out of any place
    #: the Forerunners ever built, Aurora's opened rooms included.
    await _original_printer(session, core)

    #: A world furnished before D-209 gets its base shelf: without it the
    #: capital's library would stand full of books nobody may copy.
    await _shelves(session, scenario, applied)

    #: Node distance and exit lengths (D-180): the first ring beyond the walls
    #: is twenty seconds of walking, not twenty minutes. A world created before
    #: this decision gets distance retroactively, and its edges are recomputed by it.
    gate = (
        await session.execute(select(Node).where(Node.key == "terra.capital.gate"))
    ).scalar_one_or_none()
    for key in ("terra.coal", "terra.floodplain"):
        node = (await session.execute(select(Node).where(Node.key == key))).scalar_one_or_none()
        if node is None or gate is None:
            continue
        if travel.reach_of(node) == 0:
            node.properties = {**(node.properties or {}), travel.REACH: 1}
        edge = (
            (
                await session.execute(
                    select(Edge).where(
                        ((Edge.node_a_id == gate.id) & (Edge.node_b_id == node.id))
                        | ((Edge.node_a_id == node.id) & (Edge.node_b_id == gate.id))
                    )
                )
            )
            .scalars()
            .first()
        )
        seconds = travel.frontier_seconds(constants, travel.reach_of(node))
        if edge is None:
            await travel.connect(session, gate, node, base_seconds=seconds, surface=Surface.ROAD)
        else:
            edge.base_seconds = int(seconds)
            edge.surface = Surface.ROAD

    #: Roads out of the middle of a city, laid before the doors were a rule
    #: (D-206). They are not removed: somebody walked them and somebody paved
    #: them -- their city end simply moves to the gate.
    await _reroute_through_gates(session)

    #: The mint has been renamed twice: yard -> press (D-016, together with
    #: abolishing fineness), press -> station (D-200, "станок" became "рабочая
    #: станция"), and the spaceport became a yard -- a ship is not only moored
    #: there but laid down and grown there (D-202). Existing machines learn the
    #: current name here; the migration does the same for worlds that are not
    #: reseeded.
    renamed = {
        "Монетный двор": "Монетная станция",
        "Монетный станок": "Монетная станция",
        "Автоматический станок": "Автоматическая станция",
        #: The item name, not the class word `ship.SPACEPORT`: a machine is
        #: stored by name, and the migration says the same.
        "Космодром": "Космическая верфь",
        "Верфь": "Космическая мастерская",
        #: The navigation block got a behaviour and a name with it (D-230): the
        #: ship is commanded from it, so it is called what it does.
        "Навигационный блок": "Консоль управления кораблём",
    }
    stale = (await session.execute(select(Item).where(Item.type_key.in_(renamed)))).scalars().all()
    for machine in stale:
        machine.type_key = renamed[machine.type_key]

    #: Surfaces of Pyroxis and Aurora (D-230): a world laid out while the other
    #: planets were bare dots in the sky gets somewhere to fly to.
    await surfaces(session)

    #: Buildings under already standing machines: a machine lives in a building
    #: (D-106), and nodes furnished before buildings get them retroactively.
    await _buildings(session)

    #: Deeds retroactively: land taken before the title reform is documented
    #: too (D-116). Only where there is no deed yet: a repeated run does not
    #: touch those listed for sale.

    holdings = (
        (await session.execute(select(Node).where(Node.owner_identity_id.is_not(None))))
        .scalars()
        .all()
    )
    for node in holdings:
        has_deed = await session.scalar(select(Deed.id).where(Deed.node_id == node.id).limit(1))
        if has_deed is None:
            await estate.issue_deed(session, node, node.owner_identity_id)

    await energy.ensure_pools(session, constants)
    await utility.ensure_meters(session, constants)
    await tick.ensure_scheduled(session)
    await utility.ensure_scheduled(session)
    await session.flush()


def _acct(name: str) -> dict:
    """Email, password and self-description of a starting identity from `FOUNDERS`."""
    data = FOUNDERS[name]
    return {
        "email": data["email"],
        "password": data["password"],
        "profile": {
            "surname": data["surname"],
            "age": data["age"],
            "about": data["about"],
        },
    }


async def _accounts_catch_up(session: AsyncSession) -> None:

    for name in FOUNDERS:
        identity = (
            await session.execute(select(Identity).where(Identity.name == name))
        ).scalar_one_or_none()
        if identity is None:
            continue
        acct_ = await session.get(Account, identity.account_id)
        if acct_ is None or acct_.email:
            continue
        acct = _acct(name)
        await accounts.set_credentials(session, acct_, acct["email"], acct["password"])
        if not identity.surname and not identity.about:
            accounts.apply_profile(identity, acct["profile"])
        log.info("account assigned in catch-up: %s -> %s", name, acct["email"])
    await session.flush()


async def _buildings(session: AsyncSession) -> None:
    """Place a building wherever a machine or furniture stands and there is no building.

    Idempotent: a second run adds nothing. The area is the whole plot: the
    city's built-up area is the building, it has no yard.
    """

    book = current_catalog().recipes
    rows = (
        await session.execute(
            select(Node, Item.type_key)
            .join(Container, (Container.owner_id == Node.id))
            .join(Item, Item.container_id == Container.id)
            .where(Container.kind == ContainerKind.NODE)
        )
    ).all()
    furnished: dict[str, Node] = {}
    for node, thing in rows:
        try:
            recipe = book.recipe(thing)
        except Exception:  # noqa: BLE001 -- raw material has no recipe
            continue
        if recipe.kind in (ItemKind.STATION, ItemKind.FURNITURE):
            furnished[node.key] = node
    for node in furnished.values():
        if await estate.built_area(session, node) <= 0:
            session.add(Building(node_id=node.id, area_m2=float(node.area_m2)))
    await session.flush()


async def _shelves(
    session: AsyncSession, scenario: seed_world.Scenario, applied: seed_world.Applied
) -> None:
    """The base set of every genesis library the scenario lays (D-209).

    Which recipes count as "base" is the vault's business, not this file's:
    today it is the whole ladder (D-053), and narrowing it is a data change.
    Idempotent -- rerunning adds only what is missing.
    """
    book = current_catalog().recipes
    named = set(world.station_names(world.LIBRARY))
    for spec in scenario.nodes:
        if not any(
            machine.thing_class == world.LIBRARY or machine.name in named
            for machine in spec.machines
        ):
            continue
        added = await library.stock(
            session, applied.nodes[spec.key], (recipe.name for recipe in book.recipes)
        )
        if added:
            log.info("library shelf at %s: %d recipes laid down", spec.key, added)


async def _gates_catch_up(session: AsyncSession) -> None:
    """Give every city a gate (D-206).

    The capital has had one from the first seed; a city founded by a player
    before this decision has none, and its own node becomes the gate -- that
    node **is** the whole city, so it is its own door.
    """

    cities = (await session.execute(select(City))).scalars().all()
    for city in cities:
        if await town.gate(session, city) is not None:
            continue
        delegate = await session.get(Node, city.node_id)
        if delegate is None:  # pragma: no cover -- a city without a node is a bug
            continue
        delegate.properties = {**(delegate.properties or {}), travel.EXIT: True}
        log.info("city %s got a gate by catch-up: %s", city.name, delegate.key)
    await session.flush()


async def _reroute_through_gates(session: AsyncSession) -> None:
    """Move stray edges out of a city onto its gate (D-206).

    Such an edge is a road laid before the doors became a rule: exploration used
    to tie a find to the node the scout set out from, so a trail from the
    trading yard into the wild made a second gate out of the market. The road
    itself stays -- length, surface and condition are somebody's work -- only
    its city end moves.

    An edge that would collide with an existing one is removed instead: the gate
    is already connected there, and a second road between the same two nodes
    cannot exist.
    """
    edges = (await session.execute(select(Edge))).scalars().all()
    for edge in edges:
        ends = [
            await session.get(Node, edge.node_a_id),
            await session.get(Node, edge.node_b_id),
        ]
        if any(end is None for end in ends):  # pragma: no cover -- an edge to nowhere
            continue
        a, b = ends
        cities = [await town.of_node(session, a), await town.of_node(session, b)]
        if cities[0] is not None and cities[1] is not None and cities[0].id == cities[1].id:
            continue
        for index, (end, city) in enumerate(zip(ends, cities, strict=True)):
            if city is None or await travel.is_exit(session, end):
                continue
            door = await town.gate(session, city)
            other = ends[1 - index]
            if door is None or door.id == other.id:  # pragma: no cover
                continue
            twin = (
                (
                    await session.execute(
                        select(Edge).where(
                            ((Edge.node_a_id == door.id) & (Edge.node_b_id == other.id))
                            | ((Edge.node_a_id == other.id) & (Edge.node_b_id == door.id))
                        )
                    )
                )
                .scalars()
                .first()
            )
            if twin is not None:
                await session.delete(edge)
                log.info(
                    "stray road from %s dropped: the gate already reaches %s",
                    end.name,
                    other.name,
                )
                break
            if index == 0:
                edge.node_a_id = door.id
            else:
                edge.node_b_id = door.id
            ends[index] = door
            log.info("road %s -- %s moved onto the gate %s", end.name, other.name, door.name)
    await session.flush()


async def _berths(session: AsyncSession, constants) -> None:
    """Relay the gangway of every moored ship to the length its berth deserves.

    An orbit has no pier to queue at (D-245): hulls hang beside one another,
    and the walk out is the same short spacewalk however many are parked. Left
    to the numbering, the twentieth hull over Terra would have climbed a
    gangway twenty times the first one's, at a pier that does not exist.
    """

    for vessel in (
        (await session.execute(select(Ship).where(Ship.docked_node_id.is_not(None))))
        .scalars()
        .all()
    ):
        port = await session.get(Node, vessel.docked_node_id)
        connector = await session.get(Node, vessel.connector_node_id)
        if port is None or connector is None:  # pragma: no cover
            continue
        if ship.is_orbit(port):
            vessel.berth = 1
        elif vessel.berth is None:
            vessel.berth = await ship._free_berth(session, port)
        gangway = await travel._edge_between(session, port.id, connector.id)
        if gangway is not None:
            gangway.base_seconds = int(ship._gangway_seconds(constants, vessel.berth))
    await session.flush()


async def _ship_steps(session: AsyncSession, constants) -> None:
    """Relay every corridor aboard to `ship.step_seconds` (D-240).

    Only edges with a node aboard at **both** ends: the gangway has one aboard
    and one on the pier, and its length is the berth's number -- `_berths`
    settles that one and the two rules must not fight over the same row.
    """
    aboard = select(Node.id).where(Node.properties.has_key(ship.ABOARD))
    corridors = (
        (
            await session.execute(
                select(Edge).where(Edge.node_a_id.in_(aboard), Edge.node_b_id.in_(aboard))
            )
        )
        .scalars()
        .all()
    )
    step = int(constants[R.SHIP_STEP_SECONDS])
    for edge in corridors:
        if edge.base_seconds != step:
            edge.base_seconds = step
    await session.flush()


async def _treasury(session: AsyncSession, city) -> None:
    """Put the starting money into the capital's treasury.

    The seed's only assumption about money, and it is honest: the settlement
    grant is paid **from the treasury**, and there is nowhere for it to come
    from in the first city's treasury -- taxes are not collected yet. Growth of
    the money supply goes through `genesis`, i.e. it is visible in the
    invariant check (I1).
    """

    treasury = await town.treasury(session, city)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=genesis.id,
        credit=treasury.id,
        amount=money(CITY_TREASURY_START),
        memo={"основание": "стартовый мир: казна столицы"},
    )


async def main() -> None:
    conf = settings()
    logging.basicConfig(level=conf.log_level, format="%(levelname)s %(name)s %(message)s")
    bootstrap(conf.vault_build_path)

    factory = session_factory()
    async with factory() as session, session.begin():
        await seed(session)
    await dispose()


if __name__ == "__main__":
    asyncio.run(main())
