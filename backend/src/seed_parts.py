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

from src import seed_world, sky
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
    planet: Planet
    #: Drawn, but not playable yet (D-104).
    deferred: bool = False
    #: The planet's climate (D-231): «мерзлота», «пекло» -- or nothing, where
    #: the ground keeps a body alive by itself. A property of the world rather
    #: than a constant: what a planet is, is written in the world.
    climate: str | None = None


#: The system, from the star outwards. Aquatica is here **because** it is out
#: of the alpha: what cannot be reached is shown and marked, so that a player
#: sees from the first day where the road does not go yet (50-interface/05).
#:
#: **Where each world circles is not here**: the year and the phase are the
#: vault's (`orbit.period_days`, `orbit.phase`) and the radius follows from
#: the year by Kepler -- `sky.circle_of`. They were written out here until
#: 2026-09-08, which put three balance numbers in code against D-065 and let
#: a radius be typed that the third law does not allow.
SYSTEM = (
    Orbit("pyroxis", "Пироксис", Planet.PYROXIS, climate=frost.HEAT),
    Orbit("terra", "Терра", Planet.TERRA),
    Orbit("aquatica", "Акватика", Planet.AQUATICA, deferred=True),
    Orbit("aurora", "Аврора", Planet.AURORA, climate=frost.FROST),
)


async def system(session: AsyncSession) -> Node:
    """The planets of the space layer. Returns Terra -- the alpha's home.

    A planet is an ordinary node of the same graph; the layer only decides from
    what height it is seen (D-045). What it has of its own is an **orbit**: on
    this layer a place is a function of time, so the distance between two
    planets -- and with it the length of the passage between them -- changes by
    itself, without anybody moving a node.

    There is no node above a planet (D-354, taking back D-245's orbital
    node): a hull off the pier is a place and a speed in the sky, and "in
    orbit" is a reading of that state, not somewhere to moor. A world laid
    before keeps its node until `seed_orbits.orbits_gone` takes it away.

    Idempotent, and that is what makes it a catch-up too: an existing planet
    keeps everything it carries and only learns its orbit.
    """
    constants = current()
    for circle in SYSTEM:
        radius, period, phase = sky.circle_of(constants, circle.key)
        marks: dict[str, object] = {
            world.ORBIT: {
                world.ORBIT_RADIUS: radius,
                world.ORBIT_PERIOD: period,
                world.ORBIT_PHASE: phase,
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
    return (await session.execute(select(Node).where(Node.key == "terra"))).scalar_one()


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

    Idempotent: a second run adds nothing.

    **The area is the whole plot only inside a city** (D-254). A forge is its
    plot -- the city's built-up area is the building, and it has no yard. Open
    land is the other way round: a hearth by the river is a hearth, not a wall
    across the meadow, and roofing the whole node over left the world's one
    river-fed field with nothing to plough. Outside a city the building is cut
    to what stands in it -- `build.slots_per_area` per machine, never below
    `build.area_min` -- and the rest of the plot stays yard: beds (D-246) and
    ground to walk over (D-210).

    A **spaceport** is cut the same way inside a city too (D-319): the yard's
    roof covers its machines, and the rest of the node is the apron the hulls
    set down on. Roofed over its whole plot, the capital's port would take no
    ship at all -- a hull stands on open ground the way a house does.
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
    standing: dict[str, int] = {}
    pads: set[str] = set()
    yards = frozenset(world.station_names(ship.SPACEPORT))
    for node, thing in rows:
        try:
            recipe = book.recipe(thing)
        except Exception:  # noqa: BLE001 -- raw material has no recipe
            continue
        if recipe.kind in (ItemKind.STATION, ItemKind.FURNITURE):
            furnished[node.key] = node
            standing[node.key] = standing.get(node.key, 0) + 1
        if thing in yards:
            pads.add(node.key)
    constants = current()
    for key, node in furnished.items():
        if await estate.built_area(session, node) > 0:
            continue
        whole = float(node.area_m2)
        if node.owner_city_id is None or key in pads:
            #: Room for what stands here and no more. The cap is the plot
            #: itself: a machine cannot be roofed with land the node has not got.
            whole = min(
                whole,
                max(
                    constants[R.BUILD_AREA_MIN],
                    standing[key] * constants[R.BUILD_SLOTS_PER_AREA],
                ),
            )
        session.add(Building(node_id=node.id, area_m2=whole))
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
