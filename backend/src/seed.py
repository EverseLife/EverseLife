# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The alpha's starting world: Terra's capital as it greets the first player.

Run from `backend/`: `python -m src.seed`. Running again breaks nothing -- the
world is created once and lives its own life from then on.

**This is a development scenario, not part of the game.** Everything it puts
into hands goes through `world.grant_item` with an explicit ground: matter does
not appear in the world anonymously (pillar P1), and such an arrival must be
visible in telemetry.

## The capital is assembled, not conjured (D-216)

Machines used to be handed to the capital ready-made, and that made the
starting world a second set of rules: the city the player is shown was built by
means the player does not have. Now `_machine` **assembles** -- it takes the
recipe's composition from the vault, assembles each input the same way, and so
down to raw material. Only raw material arrives from nowhere, some nine hundred
kilograms of it, as the Forerunners' legacy.

The point is not bookkeeping but the check that comes free with it: a recipe
without a composition, an input nobody makes, a circle in the ladder -- each of
them now **stops the world from being created**, loudly, instead of handing the
player a city they could not have built themselves. That is exactly how the
capital's marketplace was found to be holding a terminal the engine could not
see (D-215 named the engine's constants after classes, and the seed went on
placing them as things).

Two things the assembly does not derive, and both are deliberate. **Quality**
is declared by the seed: the Forerunners' printer is excellent and the prison's
is shoddy because the world says so, not because the proportions worked out.
And **energy** is skipped: it lives in a pool or a battery and never in a
container (D-071), so a composition that calls for it is taken as given -- the
Forerunners had current.

## The shape of the world

A city is not a place but **a group of locations connected by short edges**
(D-045, D-089). Hence the starting map: a core with the Forerunners' Printer, a
first ring with the Library, the terminal and the administration, a second with
the forge yard and free plots for houses, a face by the wall and a separate
"city exit" node (D-097), beyond which real logistics begins.

A step inside the city is seconds (`travel.city_step`), leaving the walls is
`distance 1`, i.e. `travel.frontier_step` by **road**, not offroad: coal is
hauled along it, and a city that fuels a power station built itself that road.
Beyond that every ring of distance is pricier than the previous (D-180) --
that is all the geography: going for a machine is cheap, going for coal is a
trip, reaching the frontier means fitting out an expedition.

The capital is created as an **institutional city** (D-154): it has a charter,
code-laws and a treasury, and the first player becomes its president.
Everything the authority changes afterwards it changes itself -- the seed only
sets the initial position.

Two assumptions for development time, both honestly named:

* **money is given to the city, not the player** -- there is no bank or credit
  before E4, and without money in the treasury there is nothing to pay the
  settlement grant from. Issue goes through the `genesis` account, i.e. it is
  visible in the invariant check. Players print with zero and get the grant by
  the city's decision (D-153);
* **coal is given to Tern** -- there is a coal mine on the map, but it is a
  twenty-minute walk, and one wants to see smelting today.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import ConstantError, bootstrap, current, current_catalog
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.db.base import dispose, session_factory
from src.engine import account as accounts
from src.engine import (
    breed,
    death,
    energy,
    estate,
    goods,
    justice,
    ledger,
    library,
    market,
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
from src.units import PERCENT, amount_float, money
from src.units import amount as to_amount

log = logging.getLogger("everselife.seed")

CORE = "terra.capital.core"
#: The capital's spaceport: the city's second door (D-206). A node, because
#: ship groups couple to a node -- and the `Космическая верфь` machine in it is what
#: makes the node a port (D-176).
PORT = "terra.capital.port"
#: A species, not "ore in general" (D-151): iron and copper have different veins.
IRON = "Железная руда"
COPPER = "Медная руда"
COAL = "Уголь"
PICK = "Железная кирка"
TIMBER = "Шахтная крепь"


def _one_of(thing_class: str) -> str:
    """A concrete thing of the class: a world holds things, not classes (D-215).

    The engine binds behaviour to a class -- «Терминал», «Верфь» -- and its
    constants are class names. What stands in a node is an item, and the seed
    must name one. Asked through the catalog rather than spelled out here, so a
    rename in the vault carries the seed with it.
    """
    members = current_catalog().recipes.of_class(thing_class)
    if not members:
        raise RuntimeError(f"класс «{thing_class}» пуст: стартовому миру нечего поставить")
    return members[0]


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


#: The system, from the star outwards. Aquatica is here **because** it is out
#: of the alpha: what cannot be reached is shown and marked, so that a player
#: sees from the first day where the road does not go yet (50-interface/05).
SYSTEM = (
    Orbit("pyroxis", "Пироксис", Planet.PYROXIS, 60, 11, 0.80),
    Orbit("terra", "Терра", Planet.TERRA, 136, 28, 2.10),
    Orbit("aquatica", "Акватика", Planet.AQUATICA, 172, 70, 4.00, deferred=True),
    Orbit("aurora", "Аврора", Planet.AURORA, 220, 130, 2.28),
)


async def _system(session: AsyncSession) -> Node:
    """The planets of the space layer. Returns Terra -- the alpha's home.

    A planet is an ordinary node of the same graph; the layer only decides from
    what height it is seen (D-045). What it has of its own is an **orbit**: on
    this layer a place is a function of time, so the distance between two
    planets -- and with it the length of the passage between them -- changes by
    itself, without anybody moving a node.

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
    return (await session.execute(select(Node).where(Node.key == "terra"))).scalar_one()


async def seed(session: AsyncSession) -> Node:
    """Create the starting world if it does not exist yet, otherwise bring it up to date."""
    existing = (await session.execute(select(Node).where(Node.key == CORE))).scalar_one_or_none()
    if existing is not None:
        log.info("the starting world already exists: %s", existing.key)
        await catch_up(session, existing)
        return existing

    constants = current()
    dice = random.Random(CORE)
    step = constants[R.TRAVEL_CITY_STEP]

    #: Layers are a display abstraction over one graph (D-045): Terra is seen
    #: from space, the capital from the planet, the built-up area in the city.
    #: One walks on the leaves.
    terra = await _system(session)
    capital = await world.create_node(
        session,
        "terra.capital",
        "Столица Терры",
        area_m2=1,
        layer=Layer.PLANET,
        parent=terra,
        properties={},
    )

    #: The core: every dead person comes to the bioprinter, so the city starts
    #: with it. The "forerunners" property is not decoration: by it the engine
    #: recognises that eternal machine which prints for free and slowly (D-028).
    core = await world.create_node(
        session,
        CORE,
        "Ядро: Принтер Предтеч",
        area_m2=120,
        parent=capital,
        properties={"кольцо": 0, "лес": False, "предтечи": True},
    )
    library = await world.create_node(
        session,
        "terra.capital.library",
        "Библиотека",
        area_m2=200,
        parent=capital,
        properties={"кольцо": 1},
    )
    marketplace = await world.create_node(
        session,
        "terra.capital.market",
        "Рынок",
        area_m2=200,
        parent=capital,
        properties={"кольцо": 1},
    )
    forge = await world.create_node(
        session,
        "terra.capital.forge",
        "Мастерская",
        area_m2=260,
        parent=capital,
        properties={"кольцо": 2},
    )
    face = await world.create_node(
        session,
        "terra.capital.pit",
        "Забой у стены",
        area_m2=300,
        parent=capital,
        properties={"кольцо": 3},
    )
    #: The administration is the node where the "Administration" machine
    #: stands: what a building is, is set by the machine in it (D-106).
    #: Authority lives somewhere too.
    townhall = await world.create_node(
        session,
        "terra.capital.hall",
        "Администрация",
        area_m2=180,
        parent=capital,
        properties={"кольцо": 1},
    )
    #: The city's two doors (D-206). The gate is where every road beyond the
    #: walls begins, the spaceport is where ship groups couple on; nothing else
    #: in the built-up area touches what lies outside.
    gate = await world.create_node(
        session,
        "terra.capital.gate",
        "Выход из города",
        area_m2=80,
        parent=capital,
        properties={"кольцо": 3, travel.EXIT: True},
    )
    port = await world.create_node(
        session,
        PORT,
        "Космодром",
        area_m2=240,
        parent=capital,
        properties={"кольцо": 3},
    )
    #: The first ring beyond the walls: distance 1 (D-180) -- twenty seconds of
    #: walking, not twenty minutes. Near resources are hauled daily, and that is the whole point.
    mine_ = await world.create_node(
        session,
        "terra.coal",
        "Угольная шахта",
        area_m2=400,
        layer=Layer.PLANET,
        parent=terra,
        #: Woods and stony ground by the shaft: the first axe is made here,
        #: with bare hands and without anybody's help (D-196).
        properties={"лес": True, "камни": True, "вода": "нет", travel.REACH: 1},
    )
    #: The capital's penal colony (D-174, D-176): a vein, a printer and a
    #: terminal behind one wall. The "Penal colony" machine makes the node a
    #: prison, not a property: the authority builds new penal colonies itself,
    #: like any building.
    prison = await world.create_node(
        session,
        "terra.capital.jail",
        "Каторжный забой",
        area_m2=120,
        parent=capital,
        properties={"кольцо": 3},
    )
    #: Free plots of the second ring: the city hands them out to residents
    #: (D-089), and only on own land does a craftsman place their machine (D-150).
    plots = [
        await world.create_node(
            session,
            f"terra.capital.lot{number}",
            f"Свободный участок {number}",
            area_m2=dice.uniform(*_ring(constants)),
            parent=capital,
            properties={"кольцо": 2, "участок": True},
        )
        for number in (1, 2, 3)
    ]
    #: Arable land is outside the city: land in the rings is too expensive for
    #: spelt (D-089). Place properties are rolled by the seed's hand -- the node
    #: generator arrives with exploration (D-126, D-132).
    floodplain = await world.create_node(
        session,
        "terra.floodplain",
        "Пойма у реки",
        area_m2=400,
        layer=Layer.PLANET,
        parent=terra,
        #: A meadow by the water: wild flax grows here, and fibre begins with it (D-196).
        properties={"вода": "река", "плодородие": 55, "луг": True, travel.REACH: 1},
    )

    #: Inside the city -- short edges, outside -- a real transit.
    for one, other in (
        (core, library),
        (core, marketplace),
        (core, townhall),
        (library, marketplace),
        (library, townhall),
        (marketplace, forge),
        (forge, face),
        (core, gate),
        (face, gate),
        (gate, port),
        *((forge, plot) for plot in plots),
    ):
        await travel.connect(
            session,
            one,
            other,
            base_seconds=dice.uniform(step.min, step.max),
            surface=Surface.PAVED,
        )
    #: A road leads to the mine and the floodplain, not offroad: coal and bread
    #: are hauled along it, and the city built itself these roads. Length by
    #: the node's distance (D-180).
    for dest in (mine_, floodplain):
        await travel.connect(
            session,
            gate,
            dest,
            base_seconds=travel.frontier_seconds(constants, travel.reach_of(dest)),
            surface=Surface.ROAD,
        )
    await travel.connect(
        session,
        gate,
        prison,
        base_seconds=dice.uniform(step.min, step.max),
        surface=Surface.PAVED,
    )

    #: Every vein has its own species (D-151): iron by the wall, coal and
    #: incidental copper in the mine. The copper node and the iron one are
    #: different places with different prices.
    await world.create_vein(session, face, IRON, richness=62, remaining=50_000)
    await world.create_vein(session, mine_, COAL, richness=48, remaining=30_000)
    await world.create_vein(session, mine_, COPPER, richness=41, remaining=18_000)
    await world.create_vein(session, prison, IRON, richness=35, remaining=20_000)
    await _machine(session, prison, _one_of(death.PRINTER), 50)
    await _machine(session, prison, _one_of(market.TERMINAL), 50)
    await _machine(session, prison, _one_of(justice.KATORGA), 55)

    await _machine(session, townhall, "Администрация", 65)
    #: The spaceport is a machine too, and it is what makes the node a port: a
    #: ship couples to whatever the `Космическая верфь` stands in (D-201, D-206).
    await _machine(session, port, _one_of(ship.SPACEPORT), 60)
    #: The library is a machine (D-176): the knowledge window is shown where it stands.
    await _machine(session, library, _one_of(world.LIBRARY), 70)
    #: And it holds what was put into it (D-068, D-209): the capital's shelf is
    #: the base set the Forerunners left -- today the whole catalog; a library
    #: a city builds starts empty and fills as people bring carriers.
    await _base_shelf(session, library)
    #: The Forerunners' Printer: free and twelve hours (D-028). It is also the
    #: only door into the world that never closes, hence it stands in the core.
    await _machine(session, core, _one_of(death.PRINTER), 99)
    #: The city printer at the forge: minutes instead of hours, but for energy
    #: and iron. The city sells not life but speed (D-028, D-033).
    await _machine(session, forge, _one_of(death.PRINTER), 60)
    await _machine(session, marketplace, _one_of(market.TERMINAL), 70)
    #: The mint press at the trading yard: coins are minted where they will
    #: circulate (D-016). A civic building.
    await _machine(session, marketplace, "Монетная станция", 60)
    await _machine(session, forge, "Плавильная печь", 55)
    await _machine(session, forge, "Верстак", 60)
    await _machine(session, forge, "Кузница", 65)
    #: A bed at the workshop: no own buildings before E3, the master lives at work.
    await _machine(session, forge, "Кровать", 50)
    #: A chest at the forge (D-181): the city workshop is the first place where
    #: the player sees that possessions can be put down rather than carried.
    await _machine(session, forge, "Сундук", 55)
    #: The power station is civic (D-082): it stands in the built-up area and
    #: feeds the whole city from one pool. Players haul coal to it -- without
    #: supply it is dead.
    await _machine(session, forge, "Угольная станция", 60)
    forge_yard = await world.node_container(session, forge)
    await world.grant_item(
        session,
        forge_yard,
        COAL,
        amount=200,
        quality=55,
        origin="стартовый мир: первый подвоз угля на станцию",
    )
    #: An iron stock in the printer: the city must keep it, otherwise there is
    #: nothing to print from (D-013). From then on players replenish it -- that
    #: is what makes population inflow a political question, not a backdrop.
    await world.grant_item(
        session,
        forge_yard,
        death.IRON,
        amount=50,
        quality=55,
        origin="стартовый мир: запас процессоров в биопринтере",
    )

    #: The capital is an institutional city (D-154): a charter from vault
    #: defaults, a treasury and code-laws. Everything set here the authority changes itself later.
    city = await town.found(session, current_catalog(), capital, capital.name)
    #: The prison is city land too (D-176): a state location is never a "free plot".
    #: The spaceport is city land like the gate: a door of the city is not
    #: somebody's yard, and only the authority moves the machine that makes it
    #: one (D-176, D-206).
    for node in (core, library, marketplace, forge, face, townhall, gate, port, prison, *plots):
        node.owner_city_id = city.id
    await session.flush()
    await _treasury(session, city)
    #: The city's first decision: pay newcomers a settlement grant. Written by
    #: the seed for lack of a live president in the world's first second --
    #: from then on it is an ordinary code-law, changed from the administration (D-153).
    city.laws = {"newcomer_grant": NEWCOMER_GRANT}
    await session.flush()

    #: The city pool is created at once: a city has one by construction (D-071).

    await energy.ensure_pools(session, constants)

    tern, tern_body = await world.spawn(session, "Тэрн", core, **_acct("Тэрн"))
    #: The first player is the founder: authority in the city appears with the
    #: first person, not by a separate script (D-154).
    await town.install_founder(session, city, tern)
    pocket = await world.body_container(session, tern_body)
    await world.grant_item(
        session, pocket, PICK, quality=55, origin="стартовый мир: снаряжение шахтёра"
    )
    await world.grant_item(
        session, pocket, TIMBER, amount=10, quality=50, origin="стартовый мир: снаряжение шахтёра"
    )
    await world.grant_item(
        session, pocket, COAL, amount=30, quality=58, origin="стартовый мир: запас угля"
    )
    #: The newcomer's seed fund: seeds are an item separate from the harvest
    #: (D-057). The cultivar is a base one, nobody's: everyone starts from it,
    #: and then the farmer either selects or watches the fund degrade.

    for crop, qty in (("spelt", 300), ("turnip", 200)):
        cultivar = await breed.landrace(session, current_catalog(), crop)
        await breed.seed_lot(session, current_catalog(), pocket.id, cultivar, qty, PERCENT)
    #: The newcomer's kitchen: cooking needs neither land nor capital (D-119)
    #: -- a hearth on the floodplain, utensils and products in the pocket.
    await _machine(session, floodplain, "Очаг", 70)
    #: A nursery by the floodplain: crossing needs a place, like all work (D-057).
    await _machine(session, floodplain, "Селекционный питомник", 60)
    await world.grant_item(
        session, pocket, "Глиняный горшок", quality=65, origin="стартовый мир: утварь"
    )
    for product, qty, quality in (
        ("Бобы", 10, 60),
        ("Овощи", 10, 55),
        ("Масло", 3, 50),
        ("Соль", 3, 50),
    ):
        await world.grant_item(
            session,
            pocket,
            product,
            amount=qty,
            quality=quality,
            origin="стартовый мир: продукты",
        )

    #: Refined metal to Tern: there is no gold-bearing species in the starting
    #: veins, and one wants to look at minting and fineness today -- the same
    #: assumption as with coal.
    for metal, qty in (("Аффинированное золото", 20), ("Аффинированное серебро", 60)):
        await world.grant_item(
            session,
            pocket,
            metal,
            amount=qty,
            quality=60,
            origin="стартовый мир: металл на пробу",
        )

    hyom, hyom_body = await world.spawn(session, "Хём", marketplace, **_acct("Хём"))
    hyom_pocket = await world.body_container(session, hyom_body)
    await world.grant_item(
        session,
        hyom_pocket,
        IRON,
        amount=30,
        quality=64,
        origin="стартовый мир: запас торговца",
    )
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

    #: A battery to Tern: energy does not lie in a sack, it is carried in one (D-071).
    await world.grant_item(
        session,
        pocket,
        "Аккумулятор",
        quality=55,
        origin="стартовый мир: аккумулятор",
    )

    #: The other planets' surfaces (D-230): a spaceport on Pyroxis, the ports
    #: of the abandoned city on Aurora. Laid before the buildings, so the yards
    #: there get theirs by the same rule as the capital's.
    await surfaces(session, _machine)

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


async def catch_up(session: AsyncSession, core: Node) -> None:
    """Bring an already existing world up to today's layout.

    The world is eternal, there are no wipes (D-007): "recreate the database"
    is not an answer. Here goes what cannot be added by a migration because it
    is content, not schema: the city as an institution, the administration
    building, free plots.

    Every step is idempotent: running again doubles nothing.
    """
    constants = current()
    dice = random.Random(f"{CORE}:догнать")
    step = constants[R.TRAVEL_CITY_STEP]

    capital = await session.get(Node, core.parent_id)
    if capital is None:  # pragma: no cover -- a core without a city is a bug
        return

    #: The rest of the system: a world laid out before the space layer had
    #: Terra alone in the sky, and a lone dot is not a system. The other three
    #: planets arrive with their orbits, and Terra learns its own.
    await _system(session)

    #: Berths (D-201): a ship moored before the piers were numbered has a
    #: gangway of whatever length the old rule gave it. The number itself comes
    #: from the migration, in docking order; the walk is relaid here, because
    #: what a berth is worth in seconds is the vault's business.
    await _berths(session, constants)

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

    #: Civic land: the capital's built-up area belongs to the city, and from it
    #: the city collects taxes and on it spends energy (D-149).
    children = (
        (await session.execute(select(Node).where(Node.parent_id == capital.id))).scalars().all()
    )
    for node in children:
        if node.owner_city_id is None and node.owner_identity_id is None:
            node.owner_city_id = city.id
    await session.flush()

    #: The city's doors (D-206). A world laid out before that decision has
    #: cities without a gate, and until every one of them has it a road from
    #: beyond the walls has nowhere to be tied. Done first, because the steps
    #: below draw edges themselves.
    await _gates_catch_up(session)

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
    await _machine_if_missing(session, core, _one_of(death.PRINTER), 90)

    townhall = await _node_if_missing(
        session, "terra.capital.hall", "Администрация", 180, capital, {"кольцо": 1}
    )
    if townhall is not None:
        await _machine(session, townhall, "Администрация", 65)
        townhall.owner_city_id = city.id
        await travel.connect(
            session,
            core,
            townhall,
            base_seconds=dice.uniform(step.min, step.max),
            surface=Surface.PAVED,
        )

    #: The library and the penal colony are machines (D-176): worlds furnished
    #: before that get them retroactively, and the "free plot" in their place disappears.
    library = (
        await session.execute(select(Node).where(Node.key == "terra.capital.library"))
    ).scalar_one_or_none()
    if library is not None:
        await _machine_if_missing(session, library, _one_of(world.LIBRARY), 70)
        #: A world furnished before D-209 gets its base shelf: without it the
        #: capital's library would stand full of books nobody may copy.
        await _base_shelf(session, library)
    #: The capital's penal colony (D-174, D-176): a world created before it gets
    #: the node whole -- vein, printer, terminal and the "Penal colony" machine itself.
    new_prison = await _node_if_missing(
        session,
        "terra.capital.jail",
        "Каторжный забой",
        120,
        capital,
        {"кольцо": 3},
    )
    prison = (
        new_prison
        or (
            await session.execute(select(Node).where(Node.key == "terra.capital.jail"))
        ).scalar_one_or_none()
    )
    if prison is not None:
        prison.owner_city_id = city.id
        await _machine_if_missing(session, prison, _one_of(justice.KATORGA), 55)
        await _machine_if_missing(session, prison, _one_of(death.PRINTER), 50)
        await _machine_if_missing(session, prison, _one_of(market.TERMINAL), 50)
    if new_prison is not None:
        await world.create_vein(session, new_prison, IRON, richness=35, remaining=20_000)
        output_ = (
            await session.execute(select(Node).where(Node.key == "terra.capital.gate"))
        ).scalar_one_or_none()
        if output_ is not None:
            await travel.connect(
                session,
                output_,
                new_prison,
                base_seconds=dice.uniform(step.min, step.max),
                surface=Surface.PAVED,
            )

    forge = (
        await session.execute(select(Node).where(Node.key == "terra.capital.forge"))
    ).scalar_one_or_none()
    if forge is not None:
        await _machine_if_missing(session, forge, _one_of(death.PRINTER), 60)
        #: A chest at the forge (D-181): worlds created before storages get it
        #: retroactively -- otherwise there is still nowhere to put things.
        await _machine_if_missing(session, forge, "Сундук", 55)
        yard = await world.node_container(session, forge)
        if not await _present_in(session, yard, death.IRON):
            await world.grant_item(
                session,
                yard,
                death.IRON,
                amount=50,
                quality=55,
                origin="догоняющий сид: запас процессоров в биопринтере",
            )
    for number in (1, 2, 3):
        plot = await _node_if_missing(
            session,
            f"terra.capital.lot{number}",
            f"Свободный участок {number}",
            dice.uniform(*_ring(constants)),
            capital,
            {"кольцо": 2, "участок": True},
        )
        if plot is not None:
            plot.owner_city_id = city.id
            if forge is not None:
                await travel.connect(
                    session,
                    forge,
                    plot,
                    base_seconds=dice.uniform(step.min, step.max),
                    surface=Surface.PAVED,
                )

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

    #: The capital's spaceport (D-203, D-206). A world furnished before space
    #: had nowhere to couple a ship to at all, so the node and its machine
    #: arrive whole -- by the gate, where the second door belongs.
    new_port = await _node_if_missing(session, PORT, "Космодром", 240, capital, {"кольцо": 3})
    port = (
        new_port
        or (await session.execute(select(Node).where(Node.key == PORT))).scalar_one_or_none()
    )
    if port is not None:
        port.owner_city_id = city.id
        await _machine_if_missing(session, port, _one_of(ship.SPACEPORT), 60)
        if gate is not None:
            await travel.connect(
                session,
                gate,
                port,
                base_seconds=dice.uniform(step.min, step.max),
                surface=Surface.PAVED,
            )

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
    await surfaces(session, _machine)

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


async def _base_shelf(session: AsyncSession, node: Node) -> None:
    """The base set of a genesis library: every recipe of the catalog (D-209).

    Which recipes count as "base" is the vault's business, not this file's:
    today it is the whole ladder (D-053), and narrowing it is a data change.
    Idempotent -- rerunning adds only what is missing.
    """
    book = current_catalog().recipes
    added = await library.stock(session, node, (recipe.name for recipe in book.recipes))
    if added:
        log.info("library shelf at %s: %d recipes laid down", node.key, added)


async def _machine_if_missing(session: AsyncSession, node: Node, name: str, quality: float) -> None:
    """Place a machine if the node does not have it yet. Does not create a second one."""
    yard = await world.node_container(session, node)
    if not await _present_in(session, yard, name):
        await _assemble(session, yard, name, quality=quality)


async def _present_in(session: AsyncSession, container, name: str) -> bool:

    found = await session.scalar(
        select(Item.id).where(Item.container_id == container.id, Item.type_key == name).limit(1)
    )
    return found is not None


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
    """Relay the gangway of every moored ship to the length its berth deserves."""

    for vessel in (
        (await session.execute(select(Ship).where(Ship.docked_node_id.is_not(None))))
        .scalars()
        .all()
    ):
        if vessel.berth is None:
            vessel.berth = await ship._free_berth(
                session, await session.get(Node, vessel.docked_node_id)
            )
        port = await session.get(Node, vessel.docked_node_id)
        connector = await session.get(Node, vessel.connector_node_id)
        if port is None or connector is None:  # pragma: no cover
            continue
        gangway = await travel._edge_between(session, port.id, connector.id)
        if gangway is not None:
            gangway.base_seconds = int(ship._gangway_seconds(constants, vessel.berth))
    await session.flush()


async def _node_if_missing(
    session: AsyncSession,
    key: str,
    name: str,
    area: float,
    parent: Node,
    properties: dict,
) -> Node | None:
    """Create a node if it does not exist yet. Otherwise nothing: the world is not rewritten."""
    existing_ = (await session.execute(select(Node).where(Node.key == key))).scalar_one_or_none()
    if existing_ is not None:
        return None
    return await world.create_node(
        session, key, name, area_m2=area, parent=parent, properties=properties
    )


def _ring(constants) -> tuple[float, float]:
    """Plot area spread of the first ring (D-125). Numbers come from the vault."""
    area = constants[R.LAND_AREA_RING1]
    return area.min, area.max


async def _machine(session: AsyncSession, node: Node, name: str, quality: float) -> None:
    """Поставить станцию в узел, **собрав её по рецепту** (D-216).

    Столица складывается теми же правилами, что и город игрока: у станции
    берётся её состав из вольта, у входов — их состав, и так до сырья. Из
    ниоткуда в мир приходит только сырьё, и приходит с названным основанием
    (столп П1) — дальше идёт один передел.

    Так стартовый мир перестаёт быть отдельным набором правил и становится
    проверкой основных: рецепт без состава, вход, который никто не делает,
    круг в лестнице — всё это роняет сид вслух, а не оставляет игроку мир,
    который он не смог бы повторить своими руками.
    """
    yard = await world.node_container(session, node)
    await _assemble(session, yard, name, quality=quality)


#: Энергия — не предмет (D-071): она живёт в пуле либо в аккумуляторе, и
#: положить её в контейнер нельзя. В составе она встречается (кремний плавят
#: током), поэтому сборка её пропускает: у Предтеч ток был.
INTANGIBLE = "Энергия"


def _composition(book, name: str) -> dict[str, float] | None:
    """Из чего вещь делается: состав рецепта либо расход операции.

    `None` — дальше лестницы нет: это сырьё, его берут из мира. Добывающая
    операция (рубка, добыча) расхода не имеет и потому тоже кончает спуск.
    """

    try:
        recipe = book.recipe(name)
    except ConstantError:
        recipe = None
    if recipe is not None and recipe.amounts:
        return {book.resolve(item): value for item, value in recipe.amounts.items()}
    for operation in book.operations:
        if name in operation.gives:
            spent = operation.amounts.get(name) or {}
            return {book.resolve(i): v for i, v in spent.items()} or None
    return None


async def _assemble(
    session: AsyncSession,
    container,
    name: str,
    *,
    quality: float,
    amount: float = 1.0,
    seen: tuple[str, ...] = (),
) -> None:
    """Собрать вещь в контейнере: сперва входы своим переделом, потом её саму.

    Качество объявляет сид, а не выводит лестница: мастерство Предтеч —
    это замысел мира, а не следствие пропорций. Материя же считается честно,
    по количествам вольта.
    """

    catalog = current_catalog()
    book = catalog.recipes
    name = book.resolve(name)
    if name in seen:
        raise RuntimeError("круг в лестнице: " + " → ".join((*seen, name)))
    if name == INTANGIBLE:
        return

    per_unit = _composition(book, name)
    if per_unit is None:
        await world.grant_item(
            session,
            container,
            name,
            amount=amount,
            quality=quality,
            origin="наследие Предтеч: сырьё столицы",
        )
        return

    for item, per in per_unit.items():
        if item == INTANGIBLE:
            continue
        #: Штучное уходит в работу целым (D-212): половину слитка не расходуют.
        need = goods.whole(item, per * amount, up=True, catalog=catalog)
        await _assemble(session, container, item, quality=quality, amount=need, seen=(*seen, name))
    await _spend(session, container, per_unit, amount, catalog)
    await world.grant_item(
        session,
        container,
        name,
        amount=amount,
        quality=quality,
        origin=f"наследие Предтеч: собрано по рецепту «{name}»",
    )


async def _spend(session: AsyncSession, container, per_unit: dict, units: float, catalog) -> None:
    """Списать то, что ушло в изделие. Не хватило — это дефект данных, не игры."""

    for name, per in per_unit.items():
        if name == INTANGIBLE:
            continue
        left = to_amount(goods.whole(name, per * units, up=True, catalog=catalog))
        stacks = (
            (
                await session.execute(
                    select(Item).where(Item.container_id == container.id, Item.type_key == name)
                )
            )
            .scalars()
            .all()
        )
        for stack in stacks:
            if left <= 0:
                break
            take = min(left, stack.amount)
            if take == stack.amount:
                await session.delete(stack)
            else:
                stack.amount -= take
            left -= take
        if left > 0:
            raise RuntimeError(f"на сборку не хватило «{name}»: недостаёт {amount_float(left):g}")
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
