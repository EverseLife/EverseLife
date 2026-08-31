# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The parts of the world the layout cannot describe.

Split out of `src/seed.py` along its seam: a world is either **laid** for the
first time (`seed`) or **caught up** to today (`seed_catchup`), and these are
the pieces both of them need. Every one is idempotent -- laid only if missing
-- because "run it again" must be a safe thing to do at every deploy.

What is here is what `data/world.yaml` has no way to say (D-243): the solar
system and its orbits, the Forerunners' own printer, the founders and their
development credentials (D-187), the treasury a city is founded with, the
base shelf of a genesis library (D-209) and a building under a machine that
was placed before buildings existed (D-106).
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import seed_world
from src.constants import current, current_catalog
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.engine import city as town
from src.engine import death, estate, frost, ledger, library, props, ruins, ship, world
from src.models.estate import Building
from src.models.inventory import Container, ContainerKind, Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node, Planet
from src.units import money

log = logging.getLogger("everselife.seed")

CORE = "terra.capital.core"
#: The capital's spaceport: the city's second door (D-206). A node, because
#: ship groups couple to a node -- and the `Космическая верфь` machine in it is what
#: makes the node a port (D-176).
PORT = "terra.capital.port"
#: A species, not "ore in general" (D-151): what Hyom loads onto the terminal.
IRON = "iron_ore"

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


async def system(session: AsyncSession) -> Node:
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
            #: Through the one door to the column (`props`): the merge under the
            #: row's lock is what keeps a parallel writer's key alive.
            await props.stamp(session, node, marks)
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


async def original_printer(session: AsyncSession, core: Node) -> None:
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


def account_of(name: str) -> dict:
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


async def buildings(session: AsyncSession) -> None:
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


async def shelves(
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
            session, applied.nodes[spec.key], (recipe.type_key for recipe in book.recipes)
        )
        if added:
            log.info("library shelf at %s: %d recipes laid down", spec.key, added)


async def treasury(session: AsyncSession, city) -> None:
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
        memo={"ground": "стартовый мир: казна столицы"},
    )
