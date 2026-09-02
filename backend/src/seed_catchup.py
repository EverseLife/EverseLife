# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Bringing a world that already exists up to today (D-007).

Split out of `src/seed.py` along its seam. The world is eternal and there
are no wipes: "recreate the database" is not an answer, so every rule that
changed shape after worlds had already lived under the old one leaves a
repair here. The layout itself catches up by the scenario (D-243) --
`seed_world.apply` lays whatever node, edge or machine the vault has gained;
what stays written out by hand is everything data cannot say.

**Every step is idempotent**: this runs at each deploy, and running it again
must double nothing. That is the one rule a repair here has to keep.
"""

from __future__ import annotations

import logging
import random
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import seed_parts as parts
from src import seed_world
from src.constants import current, current_catalog
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.engine import account as accounts
from src.engine import city as town
from src.engine import death, energy, estate, explore, places, props, ship, tick, travel, utility
from src.models.city import City
from src.models.estate import Building, Deed
from src.models.event import Event, EventKind
from src.models.identity import Account, Identity
from src.models.inventory import Container, ContainerKind, Item
from src.models.ship import Ship
from src.models.world import Edge, Layer, Node, Surface
from src.seed_surfaces import surfaces

log = logging.getLogger("everselife.seed")


#: Fertility of a place, by its D-251 property id (D-126). The catch-up that
#: gives soil to old city plots reads and writes exactly this key (D-246).
FERTILITY = "fertility"


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
    await parts.system(session)

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
        city.laws = {"newcomer_grant": parts.NEWCOMER_GRANT}
        await parts.treasury(session, city)
        log.info("city founded on the existing world: %s", city.name)
    #: The capital's flag and the founder's full hand (D-270): a world laid
    #: before the decision has neither, and its president would stand without
    #: the mint the decision gave the office.
    city.capital = True
    await _founder_powers_catch_up(session, city)

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
        founded.laws = {"newcomer_grant": parts.NEWCOMER_GRANT}
        await parts.treasury(session, founded)
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
    if not (core.properties or {}).get(death.PRECURSOR):
        await props.stamp(session, core, {death.PRECURSOR: True})
    #: The **original** (D-028): eternal, free, unlimited, and there will never
    #: be a second. A relic, therefore: found, not made, and not to be taken
    #: down (D-232). What prints for free is the machine, not the ground it
    #: stands on -- a mark on a node would make a free printer out of any place
    #: the Forerunners ever built, Aurora's opened rooms included.
    await parts.original_printer(session, core)

    #: A world furnished before D-209 gets its base shelf: without it the
    #: capital's library would stand full of books nobody may copy.
    await parts.shelves(session, scenario, applied)

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
            await props.stamp(session, node, {travel.REACH: 1})
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
    #: Right sides are D-251 ids: after the wave-II migration the database
    #: speaks ids, and this safety net must land strays on the same spelling.
    renamed = {
        "Монетный двор": "coin_station",
        "Монетный станок": "coin_station",
        "Автоматический станок": "auto_station",
        #: The item name, not the class word `ship.SPACEPORT`: a machine is
        #: stored by name, and the migration says the same.
        "Космодром": "space_shipyard",
        "Верфь": "space_workshop",
        #: The navigation block got a behaviour and a name with it (D-230): the
        #: ship is commanded from it, so it is called what it does.
        "Навигационный блок": "ship_console",
    }
    stale = (await session.execute(select(Item).where(Item.type_key.in_(renamed)))).scalars().all()
    for machine in stale:
        machine.type_key = renamed[machine.type_key]

    #: Surfaces of Pyroxis and Aurora (D-230): a world laid out while the other
    #: planets were bare dots in the sky gets somewhere to fly to.
    await surfaces(session)

    #: Buildings under already standing machines: a machine lives in a building
    #: (D-106), and nodes furnished before buildings get them retroactively.
    await parts.buildings(session)

    #: Roofs pulled back off open land (D-254). The rule that a building is
    #: the whole plot belongs inside a city; applied beyond the walls it
    #: roofed the world's one river-fed field over, and no bed could ever be
    #: marked on it. Only shrinking, and only where nothing was built since.
    await _unroof_open_land(session, constants)

    #: Soil under the city's plots (D-246). A plot laid or found before the
    #: rule arrived with its mark alone, and an absent property reads as nought:
    #: every plot inside every city was barren rock and grew nothing. Rolled
    #: from the node's own key, so a repeated run is the same roll and two
    #: servers replaying one world lay the same ground (D-007).
    await _soil(session, constants, scenario)

    #: Floors above the ground as nodes of their own (D-247). A house raised
    #: before that rule holds all its storeys in one node, so its upper floors
    #: are open here and the stairs cut to them. Everything that stood and lay
    #: in the house stays on the ground floor: the engine may add rooms, it may
    #: not decide for the owner what belongs upstairs.
    await _storeys(session, constants)

    #: Deeds retroactively: land taken before the title reform is documented
    #: too (D-116). Only where there is no deed yet: a repeated run does not
    #: touch those listed for sale.

    #: Plots, not every row that carries a name (D-247): a floor of a house is
    #: held with the plot under it and has no paper of its own -- issued one, it
    #: would be a storey put up for sale apart from the ground it stands on.
    holdings = (
        (
            await session.execute(
                select(Node).where(
                    Node.owner_identity_id.is_not(None),
                    Node.layer != Layer.LOCATION,
                )
            )
        )
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


async def _accounts_catch_up(session: AsyncSession) -> None:

    for name in parts.FOUNDERS:
        identity = (
            await session.execute(select(Identity).where(Identity.name == name))
        ).scalar_one_or_none()
        if identity is None:
            continue
        acct_ = await session.get(Account, identity.account_id)
        if acct_ is None or acct_.email:
            continue
        acct = parts.account_of(name)
        await accounts.set_credentials(session, acct_, acct["email"], acct["password"])
        if not identity.surname and not identity.about:
            accounts.apply_profile(identity, acct["profile"])
        log.info("account assigned in catch-up: %s -> %s", name, acct["email"])
    await session.flush()


async def _soil(session: AsyncSession, constants, scenario: seed_world.Scenario) -> None:
    """Give soil to the city plots laid before land was expected to have any (D-246).

    Only where there is none: a plot that already carries fertility keeps
    whatever the world has done to it, and a second run rolls nothing again.

    **The layout speaks first** (D-243). A plot the vault lays by hand takes the
    vault's own ground, so a world that has already lived comes out with the
    same three plots a fresh one is seeded with -- `seed_world.lay` leaves an
    existing node's properties alone on purpose, and this is the one place that
    gap is closed. Everything else is a find, and a find is rolled: from the
    node's own key, so a repeated run is the same roll and two servers replaying
    one world lay the same ground (D-007).
    """
    laid = {
        spec.key: spec.properties for spec in scenario.nodes if FERTILITY in (spec.properties or {})
    }
    plots = (
        (
            await session.execute(
                select(Node).where(
                    Node.layer == Layer.CITY,
                    Node.properties[explore.PLOT].astext == "true",
                )
            )
        )
        .scalars()
        .all()
    )
    given = 0
    for node in plots:
        if FERTILITY in (node.properties or {}):
            continue
        ground = laid.get(node.key) or await explore.civic_properties(
            session, constants, random.Random(node.key)
        )
        await props.stamp(session, node, ground)
        given += 1
    if given:
        await session.flush()
        log.info("soil given to %s city plots", given)


async def _unroof_open_land(session: AsyncSession, constants) -> None:
    """Cut a building outside a city back to what stands in it (D-254).

    The seed used to roof any node holding a machine over completely -- true
    of a forge, which *is* its plot, and false of a hearth by a river, which
    left `terra.floodplain` with nought free and unfarmable for good.

    **Only a roof the seed itself laid.** Beyond the walls anybody may build
    (`station.may_build` allows it on unowned land), and a house that covers
    its whole plot is an ordinary, legal house -- so "whole-node building on
    open ground" describes a player's home just as well as a seeded roof, and
    the two are told apart by the one thing that differs: a house somebody
    raised was raised by a job, and that job wrote `BUILDING_BUILT`. A seeded
    roof never passed through one and has no such event.

    Three further guards, each for a shape that is a whole-node building for a
    reason of its own: a cabin aboard a ship is its hull (D-234); a house of
    more than one storey holds `area = footprint * floors`, an invariant this
    step does not know how to keep; and a node with nothing standing in it was
    never roofed by `buildings()` to begin with.
    """
    #: Nodes whose building somebody built. Read once: this runs at every
    #: deploy, and asking per row would grow with the world.
    built = set(
        (
            await session.execute(
                select(Event.node_id).where(Event.kind == EventKind.BUILDING_BUILT.value)
            )
        )
        .scalars()
        .all()
    )
    roofed = (
        await session.execute(
            select(Node, Building)
            .join(Building, Building.node_id == Node.id)
            .where(
                Node.owner_city_id.is_(None),
                Building.area_m2 == Node.area_m2,
                Building.floors == 1,
            )
        )
    ).all()
    #: Machines by node in one pass, the same count `buildings()` lays roofs by.
    book = current_catalog().recipes
    standing: dict[uuid.UUID, int] = {}
    for node_id, thing in (
        await session.execute(
            select(Container.owner_id, Item.type_key)
            .join(Item, Item.container_id == Container.id)
            .where(
                Container.kind == ContainerKind.NODE,
                #: Only the candidates. Every thing in every node of the world
                #: is a scan that grows with the players, and it runs at every
                #: deploy for the sake of a handful of rows.
                Container.owner_id.in_([node.id for node, _ in roofed]),
            )
        )
    ).all():
        try:
            recipe = book.recipe(thing)
        except Exception:  # noqa: BLE001 -- raw material at the machine has no recipe
            continue
        if recipe.kind in (ItemKind.STATION, ItemKind.FURNITURE):
            standing[node_id] = standing.get(node_id, 0) + 1

    cut = 0
    for node, building in roofed:
        if node.id in built or ship.is_aboard(node) or estate.storey_of(node) is not None:
            continue
        if not standing.get(node.id):
            continue
        want = min(
            float(node.area_m2),
            max(
                constants[R.BUILD_AREA_MIN],
                standing[node.id] * constants[R.BUILD_SLOTS_PER_AREA],
            ),
        )
        if want >= float(building.area_m2):
            continue
        building.area_m2 = Decimal(str(want))
        #: One storey, so the footprint is the area: it is what the plot is
        #: measured against (D-125), and leaving it whole would free nothing.
        building.footprint_m2 = Decimal(str(want))
        cut += 1
    if cut:
        await session.flush()
        log.info("roofs cut back on %s nodes of open land", cut)


async def _storeys(session: AsyncSession, constants) -> None:
    """Open the upper floors of houses raised before they were nodes (D-247).

    Idempotent by the storeys already standing: a house that has its floors
    keeps them, and a second run cuts no second staircase.

    A single-storey house is left alone entirely -- the ground floor **is** the
    plot -- so the whole of the seeded world passes through this untouched.
    """
    plots = (
        (
            await session.execute(
                select(Node)
                .join(Building, Building.node_id == Node.id)
                .where(Building.floors > 1)
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    opened = 0
    for node in plots:
        standing = len(await estate.storeys_of(session, node))
        opened += len(await estate.open_storeys(session, constants, node)) - standing
    if opened:
        log.info("storeys opened over already standing houses: %s", opened)


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
        await props.stamp(session, delegate, {travel.EXIT: True})
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


async def _founder_powers_catch_up(session: AsyncSession, city: City) -> None:
    """The founder holds every power (D-130), the ones that arrived after this
    world was laid included: an office written with the old list is topped up
    to the current one. Only the founder's -- by identity, never by title: a
    title is the city's free word, and an appointed "president" holds exactly
    what it was given."""
    for office in await town.offices(session, city):
        if office.identity_id != city.founder_identity_id:
            continue
        held = set(str(raw) for raw in office.powers or ())
        missing = set(town.FOUNDER_POWERS) - held
        if not missing:
            continue
        office.powers = sorted(held | missing)
        log.info("founder's office topped up with %s in %s", sorted(missing), city.name)
    await session.flush()
