# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The ship: a subgraph that couples to a spaceport by one edge (D-201, D-202).

Checked is exactly what the design rests on:

* a ship is nodes of the same graph -- one walks aboard on foot, along an edge;
* the connector is one, and undocking removes that one edge and nothing else;
* an undocked ship is unreachable: no path, as to any disconnected piece of map;
* the ship grows by a node at a time, and every node is both a place and mass;
* thrust against mass decides everything: below the floor it does not tear off,
  above it the passage stretches by the very formula the summary shows;
* a passenger aboard is carried away without moving anywhere themselves.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import frost, jobs, occupation, rest, ship, storage, travel, world
from src.models.estate import Building
from src.models.identity import Body
from src.models.job import Job, JobKind, JobState
from src.models.ship import Ship
from src.models.world import Layer, Node, Planet, Surface

ENGINE = "engine_class_1"
LIFE = "life_support_system"
FUEL = "rocket_fuel"
TANK = "fuel_tank"
CONSOLE = "ship_console"


async def _orbit(session: AsyncSession, planet: Planet = Planet.TERRA) -> Node:
    """The planet's orbital node, and the planet's own node under it (D-245).

    Fetch-or-create, because every port of a planet wants the same one: the
    orbit is where a hull hangs between the ground and the sky, and there is
    exactly one of them per world.
    """
    sphere = (await select_node(session, planet.value)) or await world.create_node(
        session, planet.value, planet.value.title(), area_m2=1, planet=planet, layer=Layer.SPACE
    )
    key = ship.orbit_key(planet)
    return (await select_node(session, key)) or await world.create_node(
        session,
        key,
        f"Околопланетная орбита {planet.value}",
        area_m2=1,
        planet=planet,
        layer=Layer.SPACE,
        parent=sphere,
        properties={ship.ORBIT_NODE: True},
    )


async def select_node(session: AsyncSession, key: str) -> Node | None:
    return (await session.execute(select(Node).where(Node.key == key))).scalars().first()


async def _in_orbit(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body, vessel: Ship
) -> Ship:
    """Climb and arrive: the hull hanging over the planet it set out from."""
    job = await ship.ascend(session, constants, catalog, body, vessel)
    await ship.arrived(session, job)
    #: The climb is run by hand here, so close it by hand too: left pending it
    #: is a passage still under way, and the next order would be refused.
    job.state = JobState.DONE
    job.finished_at = job.run_at
    await session.flush()
    return vessel


async def _port(session: AsyncSession, *, name: str = "Космодром", planet=Planet.TERRA):
    """A node with a spaceport: everything a ship starts from."""
    stamp = uuid.uuid4().hex[:8]
    await _orbit(session, planet)
    node = await world.create_node(session, f"terra.port.{stamp}", name, area_m2=400, planet=planet)
    session.add(Building(node_id=node.id, area_m2=400))
    await session.flush()
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "space_shipyard", quality=60, origin="тест")
    return node


async def _shipwright(session: AsyncSession, node: Node, *, foundations: int = 1):
    identity = await world.create_identity(session, f"Корабел-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    if foundations:
        pocket = await world.body_container(session, body)
        await world.grant_item(
            session, pocket, "ship_node_foundation", amount=foundations, origin="тест"
        )
    return identity, body


async def _laid(
    session: AsyncSession, constants: Constants, body: Body, port: Node, name="Заря"
) -> Ship:
    """Lay the foundation and run the work to its end -- a ship in port."""
    job = await ship.found(session, constants, body, name)
    await ship.keel_laid(session, job)
    #: The keel job is done by hand here, so close it by hand too: left pending
    #: it stays in the queue and a later `run_one` takes it instead of the
    #: flight it was called for -- the journal hands out the earliest ready job.
    job.state = JobState.DONE
    job.finished_at = job.run_at
    await session.flush()

    mine = await ship.ships_of(session, body.identity_id)
    assert mine, "закладка кончилась кораблём"
    return mine[-1]


async def _equip(session: AsyncSession, node: Node, type_key: str, amount: float = 1):
    yard = await world.node_container(session, node)
    return await world.grant_item(session, yard, type_key, amount=amount, quality=60, origin="тест")


async def _fuel(session: AsyncSession, node: Node, amount: float):
    """Fuel aboard is fuel in a tank (D-230): a tank in the room, the fuel inside it."""
    tank = await _equip(session, node, TANK)
    inside = await storage.inside(session, tank)
    return await world.grant_item(session, inside, FUEL, amount=amount, quality=60, origin="тест")


async def _flightworthy(
    session: AsyncSession, constants: Constants, catalog: Catalog, ship_: Ship
) -> None:
    """The minimum that tears off: an engine, life support, a console and fuel in a tank."""
    connector = await session.get(Node, ship_.connector_node_id)
    await _equip(session, connector, ENGINE)
    await _equip(session, connector, LIFE)
    await _equip(session, connector, CONSOLE)
    await _fuel(session, connector, 200)


# --- the ship is nodes of the graph -----------------------------------------


async def test_foundation_gives_a_node_with_an_edge_to_the_port(
    session: AsyncSession, constants: Constants
) -> None:
    """The base, the connector and the docking point are one node (D-202).

    A node without an edge would be a piece of map nobody can reach, so the
    foundation makes both at once -- and one walks aboard on foot, as anywhere.
    """
    port = await _port(session)
    _, body = await _shipwright(session, port)
    vessel = await _laid(session, constants, body, port)

    nodes = await ship.nodes_of(session, vessel)
    assert len(nodes) == 1, "заложили одно основание — появился один узел"
    connector = nodes[0]
    assert vessel.connector_node_id == connector.id
    assert vessel.docked_node_id == port.id

    ways = await travel.exits(session, constants, port)
    assert [way.node_id for way in ways] == [connector.id], "к порту пристыкован борт"
    #: One walks aboard: an ordinary transit along an ordinary edge.
    assert await travel.depart(session, constants, body, connector) is not None


async def test_foundation_is_written_off_and_a_bare_intention_refused(
    session: AsyncSession, constants: Constants
) -> None:
    """A ship is materials, not an intention."""
    port = await _port(session)
    _, body = await _shipwright(session, port, foundations=0)
    with pytest.raises(ship.NoFoundation) as refusal:
        await ship.found(session, constants, body, "Пустышка")
    #: The refusal names a recipe, not the class: asked for the class by name,
    #: the workshop answers that nothing makes it, and the player is stuck
    #: (agents' finding, D-224). By the key and its arguments, not by the
    #: sentence: the wording is the locale's (D-251 wave III).
    assert refusal.value.key == "ship-no-foundation"
    assert "ship_node_foundation" in refusal.value.params["makes"]

    _, builder = await _shipwright(session, port, foundations=1)
    await ship.found(session, constants, builder, "Заря")
    assert not await ship._foundation_at_hand(session, builder), "основа израсходована"


async def test_foundation_only_at_a_spaceport(session: AsyncSession, constants: Constants) -> None:
    """There is nothing to couple to in a field: the first node is laid at a port."""
    bare = await world.create_node(
        session, f"terra.field.{uuid.uuid4().hex[:8]}", "Поле", area_m2=400
    )
    _, body = await _shipwright(session, bare)
    with pytest.raises(ship.NoPort):
        await ship.found(session, constants, body, "Заря")


async def test_ship_grows_by_a_node_at_a_time(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Extending joins the new node to the one it was laid from, and only to it.

    The connector stays one: the second node has no way out of the ship, so the
    inspection at the gangway cannot be walked around (D-201).
    """
    port = await _port(session)
    _, body = await _shipwright(session, port, foundations=2)
    vessel = await _laid(session, constants, body, port)
    connector = await session.get(Node, vessel.connector_node_id)

    body.node_id = connector.id
    await session.flush()
    job = await ship.extend(session, constants, body)
    await ship.keel_laid(session, job)

    nodes = await ship.nodes_of(session, vessel)
    assert len(nodes) == 2
    added = next(node for node in nodes if node.id != connector.id)
    neighbours = {way.node_id for way in await travel.exits(session, constants, added)}
    assert neighbours == {connector.id}, "новый узел висит на том, откуда закладывали"

    outward = {way.node_id for way in await travel.exits(session, constants, connector)}
    assert outward == {port.id, added.id}, "наружу по-прежнему одно ребро — коннектор"
    assert await ship.of_node(session, added) is not None

    #: A node aboard is a building from the first second, otherwise an engine
    #: would have nowhere to stand (D-106).
    from sqlalchemy import select as sql_select

    housing = (
        (await session.execute(sql_select(Building).where(Building.node_id == added.id)))
        .scalars()
        .first()
    )
    assert housing is not None and float(housing.area_m2) == constants[R.SHIP_NODE_AREA]


async def test_the_keel_is_the_bodys_own_work_and_visible_while_it_goes(
    session: AsyncSession, constants: Constants
) -> None:
    """Between the foundation leaving the pocket and the node arriving lies work.

    Eight hours of it, and until this it existed nowhere: the item was gone and
    nothing on screen said why -- which reads as a broken button rather than as
    a yard at work. The keel is an occupation of these hands like the plough
    (D-211), so it is in `all_of` -- one place where everything running is seen
    -- and it forbids a second one.
    """
    port = await _port(session)
    _, body = await _shipwright(session, port, foundations=2)

    assert await occupation.current(session, body) is None, "до закладки руки свободны"
    job = await ship.found(session, constants, body, "Заря")

    doings = {doing.kind: doing for doing in await occupation.all_of(session, body)}
    assert occupation.KEEL in doings, "закладка видна в делах"
    laying = doings[occupation.KEEL]
    assert laying.until == job.run_at, "срок тот же, что у задания"
    #: The line names the ship as an argument now, not inside a sentence
    #: assembled in Python (D-251 wave IV).
    assert laying.says.key == "doing-keel-what"
    assert laying.says.params["ship"] == "Заря", "строка называет корабль"

    #: One pair of hands lays one keel, and the second foundation stays in the
    #: pocket: a refusal must not cost material.
    with pytest.raises(occupation.Busy):
        await ship.found(session, constants, body, "Вторая")
    assert len(await ship._foundation_at_hand(session, body)) == 1, "вторая основа цела"

    #: And the yard is not a place to sleep through: the body is busy.
    with pytest.raises(occupation.Busy):
        await rest.sleep(session, constants, body)


async def test_the_keel_is_laid_by_the_worker_and_not_by_hand(
    factory: async_sessionmaker[AsyncSession], constants: Constants
) -> None:
    """The whole way through the journal, as it goes in the world.

    Every other test here calls `keel_laid` itself, so nothing checked the
    path the player actually walks: enqueue, the worker takes the job at the
    deadline, the node and its edge appear. A handler that failed there would
    have looked exactly like the reported bug -- the foundation gone and no
    node -- and no test would have said a word.
    """
    async with factory() as session, session.begin():
        port = await _port(session, name="Космодром закладки")
        _, body = await _shipwright(session, port)
        identity_id = body.identity_id
        job = await ship.found(session, constants, body, "Первая")
        term, port_id, body_id = job.run_at, port.id, body.id

    done = await jobs.run_one(factory, now=term)
    assert done is not None and done.state is JobState.DONE, done and done.last_error

    async with factory() as session:
        mine = await ship.ships_of(session, identity_id)
        assert len(mine) == 1, "закладка кончилась кораблём"
        vessel = mine[0]
        assert vessel.docked_node_id == port_id
        nodes = await ship.nodes_of(session, vessel)
        assert [node.id for node in nodes] == [vessel.connector_node_id]
        #: The node without its edge would be a piece of map nobody can reach.
        harbour = await session.get(Node, port_id)
        ways = {way.node_id for way in await travel.exits(session, constants, harbour)}
        assert ways == {vessel.connector_node_id}, "к порту пристыкован борт"
        #: And the hands are free again: the work is over, not still counted.
        builder = await session.get(Body, body_id)
        assert await occupation.current(session, builder) is None, "закладка кончилась"


async def test_the_connector_stays_the_only_way_in(
    session: AsyncSession, constants: Constants
) -> None:
    """Nothing may grow a second edge out of a ship (D-201).

    Exploration lays an edge from the node one leaves from, so a run from
    aboard would quietly weld the ship to a wild node -- a second entrance past
    the gangway inspection. The same for laying a foundation onto a hull: that
    would be a second ship welded to the first for good.
    """
    from src.engine import explore

    port = await _port(session)
    _, body = await _shipwright(session, port, foundations=2)
    vessel = await _laid(session, constants, body, port)
    body.node_id = vessel.connector_node_id
    await session.flush()

    with pytest.raises(explore.ExploreError):
        await explore.survey(session, constants, body)

    #: A spaceport aboard changes nothing: a ship is grown from the inside.
    connector = await session.get(Node, vessel.connector_node_id)
    await _equip(session, connector, "space_shipyard")
    with pytest.raises(ship.NoPort):
        await ship.found(session, constants, body, "Второй")


async def test_extending_somebody_elses_ship_refused(
    session: AsyncSession, constants: Constants
) -> None:
    """A ship belongs to a person: a stranger neither builds it nor moves it."""
    port = await _port(session)
    _, owner = await _shipwright(session, port, foundations=1)
    vessel = await _laid(session, constants, owner, port)

    _, stranger = await _shipwright(session, port, foundations=1)
    stranger.node_id = vessel.connector_node_id
    await session.flush()
    with pytest.raises(ship.NotYours):
        await ship.extend(session, constants, stranger)


# --- thrust against mass -----------------------------------------------------


async def test_every_node_is_both_a_place_and_mass(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Mass is the nodes plus everything aboard -- both are the player's decisions."""
    port = await _port(session)
    _, body = await _shipwright(session, port, foundations=2)
    vessel = await _laid(session, constants, body, port)

    bare = await ship.mass(session, constants, catalog, vessel)
    assert bare == pytest.approx(constants[R.SHIP_NODE_MASS])

    connector = await session.get(Node, vessel.connector_node_id)
    body.node_id = connector.id
    await session.flush()
    job = await ship.extend(session, constants, body)
    await ship.keel_laid(session, job)
    assert await ship.mass(session, constants, catalog, vessel) == pytest.approx(
        2 * constants[R.SHIP_NODE_MASS]
    ), "второй узел добавил ровно свою массу"

    #: Cargo weighs as well, and a chest does not hide it.
    await _equip(session, connector, "iron_ingot", amount=100)
    assert await ship.mass(session, constants, catalog, vessel) > 2 * constants[R.SHIP_NODE_MASS]


async def test_thrust_and_class_come_from_the_vault_by_name(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The engine keeps no list of engines: thrust is `ship.thrust` by item name."""
    port = await _port(session)
    _, body = await _shipwright(session, port)
    vessel = await _laid(session, constants, body, port)
    connector = await session.get(Node, vessel.connector_node_id)

    assert await ship.thrust(session, constants, vessel) == 0
    assert await ship.engine_class(session, constants, vessel) is None

    await _equip(session, connector, ENGINE)
    assert await ship.thrust(session, constants, vessel) == pytest.approx(
        constants[R.SHIP_THRUST][ENGINE]
    )
    assert await ship.engine_class(session, constants, vessel) == 1


async def test_passage_stretches_by_mass_and_has_a_ceiling(constants: Constants) -> None:
    """Time is the table time times reference-over-actual, and never below the floor."""
    table = 24.0
    reference = constants[R.SHIP_REFERENCE_RATIO]
    #: Exactly at the reference the passage takes the table time.
    assert ship.passage_hours(constants, table, reference) == pytest.approx(table)
    #: Half the thrust-to-mass -- twice the time.
    assert ship.passage_hours(constants, table, reference / 2) == pytest.approx(2 * table)
    #: However much thrust is hung on, the ceiling holds.
    floor = table * constants[R.SHIP_ROUTE_MIN_SHARE] / 100
    assert ship.passage_hours(constants, table, reference * 100) == pytest.approx(floor)


# --- a ship is no short cut across the land -----------------------------------


async def test_docking_leaves_land_measurements_alone(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A gangway neither shortens nor lengthens any road ashore (D-201).

    Land is priced by the distance to the city's printer (D-220), and that
    distance is written down rather than walked for. A ship hangs on the map by
    its one gangway, and no ship node belongs to a city -- so casting off and
    mooring must leave what is written exactly as it was. Without this the
    whole world would re-measure itself every time somebody put out to space.
    """
    from src.engine import city as town
    from src.engine import estate

    #: A town around the port: a core with the printer the city grew from, and
    #: the port one step away from it. Without a city there is no centre to
    #: measure from, and nothing for the test to hold on to.
    stamp = uuid.uuid4().hex[:8]
    delegate = await world.create_node(
        session, f"terra.spacetown.{stamp}", "Портовый", area_m2=1, layer=Layer.PLANET
    )
    core = await world.create_node(
        session,
        f"terra.spacetown.{stamp}.core",
        "Ядро",
        area_m2=100,
        parent=delegate,
        properties={"ring": 0, "precursors": True},
    )
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, world.BIOPRINTER, quality=60, origin="тест")

    port = await _port(session)
    port.parent_id = delegate.id
    await session.flush()
    await travel.connect(session, core, port, base_seconds=30, surface=Surface.PAVED)
    city = await town.found(session, catalog, delegate, "Портовый")
    for node in (core, port):
        node.owner_city_id = city.id
    await session.flush()

    _, body = await _shipwright(session, port)
    vessel = await _laid(session, constants, body, port)
    await _flightworthy(session, constants, catalog, vessel)

    measured = await estate.nodes_from_center(session, port, city)
    assert measured == 1, "порт в шаге от ядра"
    assert port.center_steps is not None

    connector = await session.get(Node, vessel.connector_node_id)
    body.node_id = connector.id
    await session.flush()
    await ship.ascend(session, constants, catalog, body, vessel)

    assert port.center_steps == measured, "отход корабля не трогает землю"


# --- casting off is the removal of one edge ----------------------------------


async def test_the_climb_removes_the_edge_and_the_ship_becomes_unreachable(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The flight is the absence of an edge, not a state of the body (D-201)."""
    port = await _port(session)
    _, body = await _shipwright(session, port)
    vessel = await _laid(session, constants, body, port)
    await _flightworthy(session, constants, catalog, vessel)

    connector = await session.get(Node, vessel.connector_node_id)
    body.node_id = connector.id
    await session.flush()

    await ship.ascend(session, constants, catalog, body, vessel)
    assert vessel.docked_node_id is None
    assert await travel.exits(session, constants, port) == ()
    assert await travel.exits(session, constants, connector) == (), (
        "у отстыкованного борта нет ни одного ребра наружу"
    )

    #: A passenger left ashore cannot get to the ship: no path, as to any
    #: disconnected piece of the map.
    _, ashore = await _shipwright(session, port, foundations=0)
    with pytest.raises(travel.NoEdge):
        await travel.depart(session, constants, ashore, connector)


async def test_overloaded_ship_does_not_tear_off(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Below `ship.min_thrust_ratio` it does not lift at all -- and says so."""
    port = await _port(session)
    _, body = await _shipwright(session, port)
    vessel = await _laid(session, constants, body, port)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    body.node_id = connector.id
    await session.flush()

    #: Enough cargo for the thrust-to-mass to drop below the floor.
    await _equip(session, connector, "iron_ingot", amount=100_000)
    assert (
        await ship.ratio(session, constants, catalog, vessel) < constants[R.SHIP_MIN_THRUST_RATIO]
    )
    with pytest.raises(ship.NotEnoughThrust):
        await ship.ascend(session, constants, catalog, body, vessel)
    assert vessel.docked_node_id == port.id, "перегруженный корабль остался в порту"


async def test_crew_beyond_life_support_does_not_fly(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Life support decides how many people the ship holds, and it is checked before the flight."""
    port = await _port(session)
    _, body = await _shipwright(session, port)
    vessel = await _laid(session, constants, body, port)
    connector = await session.get(Node, vessel.connector_node_id)
    await _equip(session, connector, ENGINE)
    await _equip(session, connector, CONSOLE)
    body.node_id = connector.id
    await session.flush()

    await _fuel(session, connector, 200)
    with pytest.raises(ship.NoLifeSupport):
        await ship.ascend(session, constants, catalog, body, vessel)

    await _equip(session, connector, LIFE)
    assert await ship.ascend(session, constants, catalog, body, vessel) is not None


async def test_the_climb_without_fuel_to_come_back_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hull in orbit is unreachable, so climbing dry would be a trap.

    Nobody can bring fuel to a ship with no edges, and nobody aboard can walk
    off. So the fuel for the descent back onto this very planet is checked
    before the gangway comes off (D-245): the climb is charged now, the way
    down is only guaranteed.
    """
    port = await _port(session)
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    connector = await session.get(Node, vessel.connector_node_id)
    await _equip(session, connector, ENGINE)
    await _equip(session, connector, LIFE)
    await _equip(session, connector, CONSOLE)
    owner.node_id = connector.id
    await session.flush()

    with pytest.raises(ship.NoFuel):
        await ship.ascend(session, constants, catalog, owner, vessel)
    assert vessel.docked_node_id == port.id, "сухой корабль остался у причала"

    await _fuel(session, connector, 200)
    assert await ship.ascend(session, constants, catalog, owner, vessel) is not None


async def test_gangway_is_not_pulled_from_under_a_walker(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Somebody is walking the gangway: the climb waits (D-201)."""
    async with factory() as session, session.begin():
        port = await _port(session)
        _, owner = await _shipwright(session, port)
        vessel = await _laid(session, constants, owner, port)
        await _flightworthy(session, constants, catalog, vessel)
        connector = await session.get(Node, vessel.connector_node_id)
        owner.node_id = connector.id
        await session.flush()

        #: A guest sets out aboard -- and is on the edge right now.
        _, guest = await _shipwright(session, port, foundations=0)
        await travel.depart(session, constants, guest, connector)

        with pytest.raises(travel.EdgeInUse):
            await ship.ascend(session, constants, catalog, owner, vessel)


async def test_a_stranger_does_not_lift_your_ship(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    port = await _port(session)
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    await _flightworthy(session, constants, catalog, vessel)

    _, guest = await _shipwright(session, port, foundations=0)
    guest.node_id = vessel.connector_node_id
    await session.flush()
    with pytest.raises(ship.NotYours):
        await ship.ascend(session, constants, catalog, guest, vessel)


# --- the passage -------------------------------------------------------------


async def test_a_landing_moors_at_the_chosen_pad_and_carries_the_passenger(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Two ports of one planet are reached by climbing and coming down (D-245).

    There is no corridor from a planet to itself any more: the hull goes up to
    the orbit and picks its pad from there, which is the moment a crew actually
    knows what it is choosing between.

    The passenger goes nowhere themselves -- they stand in their node all the
    way, and it is the node's neighbour that changes (D-201).
    """
    async with factory() as session, session.begin():
        here = await _port(session, name="Космодром столицы")
        there = await _port(session, name="Дальний космодром")
        _, owner = await _shipwright(session, here)
        vessel = await _laid(session, constants, owner, here)
        await _flightworthy(session, constants, catalog, vessel)
        connector = await session.get(Node, vessel.connector_node_id)
        owner.node_id = connector.id
        await session.flush()

        fuel_before = await ship.fuel_aboard(session, vessel)
        await _in_orbit(session, constants, catalog, owner, vessel)
        assert vessel.docked_node_id == (await _orbit(session)).id, "борт на орбите"
        flight = await ship.land(session, constants, catalog, owner, vessel, there)
        assert await ship.fuel_aboard(session, vessel) < fuel_before, "рейс сжёг топливо"
        term, ship_id, owner_id = flight.run_at, vessel.id, owner.id
        connector_id, there_id = connector.id, there.id

    assert await jobs.run_one(factory, now=term) is not None

    async with factory() as session:
        vessel = await session.get(Ship, ship_id)
        assert vessel.docked_node_id == there_id, "корабль пристыкован в другом порту"
        passenger = await session.get(Body, owner_id)
        assert passenger.node_id == connector_id, "пассажир никуда не переходил"
        #: And the node's neighbour is now the other port -- that is the whole flight.
        arrived_node = await session.get(Node, connector_id)
        ways = {way.node_id for way in await travel.exits(session, constants, arrived_node)}
        assert ways == {there_id}


async def test_a_ship_under_way_takes_no_second_order(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One hull, one leg.

    Casting off leaves the ship with no edge at all, and "not docked" was the
    only thing the order asked -- so a second order given while the first was
    still under way was taken: the fuel was burnt twice and two arrivals stood
    in the journal, each ready to set the same hull down in its own port.
    """
    here = await _port(session, name="Космодром столицы")
    elsewhere = await _port(session, name="Третий космодром")
    _, owner = await _shipwright(session, here)
    vessel = await _laid(session, constants, owner, here)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()

    await ship.ascend(session, constants, catalog, owner, vessel)
    burnt = await ship.fuel_aboard(session, vessel)

    with pytest.raises(ship.InFlight):
        await ship.land(session, constants, catalog, owner, vessel, elsewhere)
    with pytest.raises(ship.InFlight):
        await ship.ascend(session, constants, catalog, owner, vessel)
    assert await ship.fuel_aboard(session, vessel) == burnt, "отказ всё равно сжёг топливо"


async def test_two_orders_in_one_second_send_the_ship_once(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Two sockets of one player, or an AI citizen (D-224), pressing together.

    A check-then-act without a lock lets both pass and both queue a leg: the
    fuel goes twice and the hull is set down twice. The row is held while the
    decision is made, so the second order waits for the first and is refused by
    what it finds.
    """
    async with factory() as session, session.begin():
        here = await _port(session, name="Космодром столицы")
        _, owner = await _shipwright(session, here)
        vessel = await _laid(session, constants, owner, here)
        await _flightworthy(session, constants, catalog, vessel)
        connector = await session.get(Node, vessel.connector_node_id)
        owner.node_id = connector.id
        await session.flush()
        ship_id, owner_id = vessel.id, owner.id
        fuel_before = await ship.fuel_aboard(session, vessel)

    #: Both transactions must be open and looking at the same hull before
    #: either writes -- that is the window a check-then-act loses the ship in.
    #: Without the barrier the first order simply commits before the second
    #: starts, and the test would pass with no lock at all.
    ready = asyncio.Barrier(2)

    async def order() -> str:
        async with factory() as db, db.begin():
            mine = await db.get(Ship, ship_id)
            me = await db.get(Body, owner_id)
            await ready.wait()
            try:
                await ship.ascend(db, constants, catalog, me, mine)
            except ship.InFlight:
                return "refused"
            return "flew"

    answers = await asyncio.gather(order(), order())
    assert sorted(answers) == ["flew", "refused"], f"оба приказа прошли: {answers}"

    async with factory() as session:
        vessel = await session.get(Ship, ship_id)
        flights = (
            (
                await session.execute(
                    select(Job).where(
                        Job.kind == JobKind.SHIP_FLIGHT.value,
                        Job.payload["ship"].astext == str(ship_id),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(flights) == 1, "в журнале два рейса одного корпуса"
        spent = fuel_before - await ship.fuel_aboard(session, vessel)
        assert spent > 0, "рейс не сжёг топлива"


async def test_no_route_is_closed_by_the_class_of_the_engine(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Class is power and efficiency, never a licence for a route (D-235).

    The world had it the other way round once: Aurora asked for a second-class
    engine and Pyroxis for a third, and the ladder held exactly one engine, of
    the first. Both planets were shut from the outside -- every mechanic on
    them worked, and nobody could ever get there.

    Now the weakest engine aboard flies anywhere the sky allows. What it costs
    is another matter, and the console says so before the attempt.
    """
    here = await _port(session)
    await _port(session, name="Порт Авроры", planet=Planet.AURORA)
    far = await _orbit(session, Planet.AURORA)
    _, owner = await _shipwright(session, here)
    vessel = await _laid(session, constants, owner, here)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    await _fuel(session, connector, 5000)
    owner.node_id = connector.id
    await session.flush()

    await _in_orbit(session, constants, catalog, owner, vessel)
    summary = await ship.profile(session, constants, catalog, vessel)
    aurora = next(route for route in summary["routes"] if route["node"] == far.key)
    assert aurora["reachable"], "класс больше не запирает маршрут"
    assert aurora["hours"] > 0 and aurora["fuel"] > 0
    assert await ship.fly(session, constants, catalog, owner, vessel, far) is not None


def test_a_better_class_burns_less_for_the_same_passage(constants: Constants) -> None:
    """The other half of D-235: the reward for a better engine is the bill.

    Nothing is unlocked by class any more, so the whole of what a higher class
    buys has to be visible in the numbers -- fuel here, and hours through the
    thrust it adds.
    """
    from src.engine.ship.physics import efficiency, fuel_for

    weak = fuel_for(constants, weight=10_000, hours=100, klass=1)
    strong = fuel_for(constants, weight=10_000, hours=100, klass=3)
    assert strong < weak, "третий класс обязан жечь меньше первого"
    assert efficiency(constants, 1) == 1, "первый класс — базовая линия расхода"
    #: And an unknown class is the baseline rather than a free flight.
    assert fuel_for(constants, weight=10_000, hours=100) == weak


async def test_ship_takes_the_planet_of_the_port_it_stands_at(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """After a passage the ship is where it flew to, and prices its way home from there.

    Its nodes carry the planet of the port they stand at, not of the shipyard:
    otherwise a ship that reached Aurora would price the way back as a local
    hop between two Terran ports.

    The second-class engine is granted by hand deliberately: there is no recipe
    for it on the ladder yet, and the test shows exactly what the vault
    promises -- thrust and class come from the data by name, so an engine that
    appears there flies without a code change.
    """
    async with factory() as session, session.begin():
        home = await _port(session)
        await _port(session, name="Порт Авроры", planet=Planet.AURORA)
        far = await _orbit(session, Planet.AURORA)
        _, owner = await _shipwright(session, home)
        vessel = await _laid(session, constants, owner, home)
        connector = await session.get(Node, vessel.connector_node_id)
        await _equip(session, connector, "Двигатель II класса")
        await _equip(session, connector, LIFE)
        await _equip(session, connector, CONSOLE)
        await _fuel(session, connector, 2000)
        owner.node_id = connector.id
        await session.flush()

        await _in_orbit(session, constants, catalog, owner, vessel)
        flight = await ship.fly(session, constants, catalog, owner, vessel, far)
        #: The interplanetary passage is days, not the hours of a climb.
        assert flight.run_at > flight.created_at
        term, ship_id = flight.run_at, vessel.id
        home_key = ship.orbit_key(Planet.TERRA)

    assert await jobs.run_one(factory, now=term) is not None

    async with factory() as session:
        vessel = await session.get(Ship, ship_id)
        connector = await session.get(Node, vessel.connector_node_id)
        assert connector.planet is Planet.AURORA, "корабль стоит там, куда прилетел"

        summary = await ship.profile(session, constants, catalog, vessel)
        back = next(route for route in summary["routes"] if route["node"] == home_key)
        #: The way back is an interplanetary passage, not a local hop: the sky
        #: between the two is what it costs, and it is priced in hours and fuel
        #: rather than in a class of engine somebody must own (D-235).
        assert back["hours"] > 0 and back["fuel"] > 0, (
            "обратный рейс считается межпланетным, а не местным"
        )


async def _sphere(
    session: AsyncSession,
    key: str,
    planet: Planet,
    *,
    radius: float,
    period: float,
    phase: float = 0.0,
) -> Node:
    """A planet on the space layer: a node whose whole point is its orbit."""
    return await world.create_node(
        session,
        key,
        key.title(),
        planet=planet,
        area_m2=1,
        layer=Layer.SPACE,
        properties={
            world.ORBIT: {
                world.ORBIT_RADIUS: radius,
                world.ORBIT_PERIOD: period,
                world.ORBIT_PHASE: phase,
            }
        },
    )


async def test_passage_time_follows_the_sky(session: AsyncSession, constants: Constants) -> None:
    """The same route costs differently at different hours (D-037).

    Two planets started level: at the epoch they stand on one side of the star
    and the way between them is the shortest it ever gets; two days later the
    inner one has gone half a circle and stands opposite, and the way is the
    longest. Everything in between is the sky's doing.

    The outer planet is given an unreachably long year on purpose -- with one
    of the two standing still the configuration is exactly known, and the test
    checks the rule rather than arithmetic on two moving bodies.
    """
    await _sphere(session, "terra", Planet.TERRA, radius=100, period=4)
    await _sphere(session, "aurora", Planet.AURORA, radius=200, period=1_000_000)
    await session.flush()

    origin = await world.epoch(session)
    assert origin is not None
    window = constants[R.SHIP_ROUTE_WINDOW_HOURS]["aurora-terra"]
    apart = constants[R.SHIP_ROUTE_APART_HOURS]["aurora-terra"]

    together = await ship.base_hours(session, constants, Planet.TERRA, Planet.AURORA, at=origin)
    opposite = await ship.base_hours(
        session, constants, Planet.TERRA, Planet.AURORA, at=origin + timedelta(days=2)
    )
    between = await ship.base_hours(
        session, constants, Planet.TERRA, Planet.AURORA, at=origin + timedelta(days=1)
    )

    assert together == pytest.approx(window, rel=1e-3), (
        "в сближение рейс идёт по короткому краю вольта"
    )
    assert opposite == pytest.approx(apart, rel=1e-3), "в противостояние — по длинному"
    assert window < between < apart, "между краями время идёт по расстоянию"


async def test_berths_are_numbered_and_the_lowest_free_one_is_taken(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The gangway is as long as the berth's number, and a freed berth is refilled.

    Three ships at one yard stand at berths one, two and three, and the walk to
    each is exactly that many seconds. The middle one casts off -- and the next
    arrival takes **its** place rather than a fourth: a port that has seen ships
    come and go all day still boards the next one close to the door.
    """
    port = await _port(session)
    berths: list[Ship] = []
    for number in range(3):
        _, builder = await _shipwright(session, port)
        vessel = await _laid(session, constants, builder, port, name=f"Борт-{number}")
        berths.append(vessel)

    assert [vessel.berth for vessel in berths] == [1, 2, 3], "места раздаются по порядку"
    for vessel in berths:
        connector = await session.get(Node, vessel.connector_node_id)
        way = next(
            path
            for path in await travel.exits(session, constants, port)
            if path.node_id == connector.id
        )
        assert way.seconds == pytest.approx(
            vessel.berth * constants[R.SHIP_BERTH_SECONDS] * constants[R.ROAD_PAVED_MULTIPLIER]
        ), "трап длиной в номер места"

    #: The middle ship leaves, and its berth is the one the next arrival gets.
    middle = berths[1]
    aboard = await session.get(Node, middle.connector_node_id)
    await _flightworthy(session, constants, catalog, middle)
    holder = await _body_of(session, middle)
    holder.node_id = aboard.id
    await session.flush()
    await ship.ascend(session, constants, catalog, holder, middle)
    assert middle.berth is None, "в полёте места у причала нет"

    _, latecomer = await _shipwright(session, port)
    arrival = await _laid(session, constants, latecomer, port, name="Опоздавший")
    assert arrival.berth == 2, "освободившееся место занимает следующий пришедший"


async def _body_of(session: AsyncSession, vessel: Ship) -> Body:
    """The ship's owner's body -- the only one that may command it."""
    from sqlalchemy import select as sql_select

    return (
        (
            await session.execute(
                sql_select(Body).where(Body.identity_id == vessel.owner_identity_id)
            )
        )
        .scalars()
        .one()
    )


async def test_a_crossing_needs_more_fuel_than_the_climb(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Fuel goes by mass and by days: enough to reach orbit is not enough for a world away.

    The climb is affordable by construction -- it already checked the fuel for
    the way back down, and that is the whole of a local journey (D-245). What a
    short tank does not buy is the days of an interplanetary passage.
    """
    port = await _port(session)
    await _port(session, name="Порт Авроры", planet=Planet.AURORA)
    far = await _orbit(session, Planet.AURORA)
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    connector = await session.get(Node, vessel.connector_node_id)
    await _equip(session, connector, "Двигатель II класса")
    await _equip(session, connector, LIFE)
    await _equip(session, connector, CONSOLE)
    #: Enough for the climb and the descent behind it several times over, and
    #: nowhere near enough for a world away. The interplanetary passage is hours
    #: to days rather than a fixed number of days (D-037): its price depends on
    #: where the planets stand, and this tank is short of even the shortest
    #: window.
    await _fuel(session, connector, 4)
    owner.node_id = connector.id
    await session.flush()

    await _in_orbit(session, constants, catalog, owner, vessel)
    with pytest.raises(ship.NoFuel):
        await ship.fly(session, constants, catalog, owner, vessel, far)


async def test_the_ground_does_not_cross_and_a_climb_does_not_climb_twice(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Three stages, and each refuses the others' action in words (D-245)."""
    port = await _port(session)
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    await _flightworthy(session, constants, catalog, vessel)
    owner.node_id = vessel.connector_node_id
    await session.flush()

    #: From the pad one only climbs: between worlds a hull goes orbit to orbit.
    with pytest.raises(ship.Docked):
        await ship.fly(session, constants, catalog, owner, vessel, await _orbit(session))
    #: And there is nothing to come down from.
    with pytest.raises(ship.Docked):
        await ship.land(session, constants, catalog, owner, vessel, port)
    await ship.ascend(session, constants, catalog, owner, vessel)
    with pytest.raises(ship.InFlight):
        await ship.ascend(session, constants, catalog, owner, vessel)


async def test_summary_names_the_price_before_the_attempt(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A refusal by mass must not be a surprise sprung after the hold is loaded (D-202).

    And what the console offers depends on where the hull is (D-245): from the
    pad there is one move, and it is the climb.
    """
    port = await _port(session)
    there = await _port(session, name="Второй космодром")
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    await _flightworthy(session, constants, catalog, vessel)
    owner.node_id = vessel.connector_node_id
    await session.flush()

    summary = await ship.profile(session, constants, catalog, vessel)
    assert summary["nodes"] == 1
    assert summary["thrust"] > 0 and summary["mass"] > 0
    assert summary["ratio"] == pytest.approx(summary["thrust"] / summary["mass"], rel=1e-2)
    assert summary["docked"] == port.key
    assert summary["stage"] == "port", "борт стоит в космодроме"
    climb = summary["climb"]
    assert climb["node"] == ship.orbit_key(Planet.TERRA)
    assert climb["reachable"] and climb["hours"] > 0 and climb["fuel"] > 0
    #: The descent home is guaranteed but not charged: `needs` is the larger.
    assert climb["needs"] > climb["fuel"]
    assert summary["routes"] == [] and summary["landings"] == [], "с земли выбирать нечего"

    #: And from orbit the pads appear, this planet's own.
    await _in_orbit(session, constants, catalog, owner, vessel)
    aloft = await ship.profile(session, constants, catalog, vessel)
    assert aloft["stage"] == "orbit" and aloft["climb"] is None
    pads = {pad["node"] for pad in aloft["landings"]}
    assert pads == {port.key, there.key}, "с орбиты видно оба космодрома планеты"
    #: One price for the whole planet, beside the list rather than copied into
    #: every row of it (D-225, D-245): a pad differs from a pad in its name and
    #: in nothing the console could charge for.
    assert all(set(pad) <= {"node", "name", "anywhere"} for pad in aloft["landings"])
    down = aloft["descent"]
    assert down["hours"] > 0 and down["reachable"]
    #: The ground is the one place a hull may stand with dry tanks: nothing is
    #: kept back from a descent.
    assert down["needs"] == down["fuel"]


# --- the console, the tanks and the ship's card (D-230) -----------------------


async def test_ship_is_commanded_from_the_console(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The climb is ordered at the bridge: aboard is not enough, and the
    console must stand in the very room the owner stands in."""
    port = await _port(session)
    _, owner = await _shipwright(session, port, foundations=2)
    vessel = await _laid(session, constants, owner, port)
    connector = await session.get(Node, vessel.connector_node_id)
    await _equip(session, connector, ENGINE)
    await _equip(session, connector, LIFE)
    await _fuel(session, connector, 200)
    owner.node_id = connector.id
    await session.flush()

    with pytest.raises(ship.NoConsole):
        await ship.ascend(session, constants, catalog, owner, vessel)

    #: The console in the next room: still not this one.
    job = await ship.extend(session, constants, owner)
    await ship.keel_laid(session, job)
    hold = next(n for n in await ship.nodes_of(session, vessel) if n.id != connector.id)
    await _equip(session, hold, CONSOLE)
    with pytest.raises(ship.NoConsole):
        await ship.ascend(session, constants, catalog, owner, vessel)

    owner.node_id = hold.id
    await session.flush()
    assert await ship.ascend(session, constants, catalog, owner, vessel) is not None


async def test_fuel_in_a_canister_is_cargo_not_reserve(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The engines draw from the tanks (D-230). A canister of fuel lying in the
    hold weighs like any cargo and buys no passage."""
    port = await _port(session)
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    connector = await session.get(Node, vessel.connector_node_id)
    bare = await ship.mass(session, constants, catalog, vessel)

    canister = await _equip(session, connector, "canister")
    inside = await storage.inside(session, canister)
    await world.grant_item(session, inside, FUEL, amount=5, quality=60, origin="тест")
    assert await ship.fuel_aboard(session, vessel) == 0
    assert await ship.mass(session, constants, catalog, vessel) > bare, "канистра с топливом весит"

    await _fuel(session, connector, 40)
    assert await ship.fuel_aboard(session, vessel) == pytest.approx(40)


async def test_card_lists_engines_and_where_the_mass_comes_from(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The console shows what to cut and what to add: engines one by one and
    the mass split into hull, machines and cargo (D-230)."""
    port = await _port(session)
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    await _equip(session, connector, "pipe", amount=10)

    card = await ship.profile(session, constants, catalog, vessel)
    assert card["engines"] == [
        {
            "name": ENGINE,
            "count": 1,
            "thrust": constants[R.SHIP_THRUST][ENGINE],
            "class": 1,
        }
    ]
    parts = card["mass_parts"]
    assert parts["hull"] == constants[R.SHIP_NODE_MASS]
    assert parts["machines"] > 0 and parts["cargo"] > 0
    assert card["mass"] == pytest.approx(sum(parts.values()), abs=0.1)


# --- the ground console, and turning back (D-242) -----------------------------


GROUND = "ground_console"


async def _ground_console(session: AsyncSession, node: Node) -> None:
    """A ground console standing in a node: the second place an order comes from."""
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, GROUND, quality=60, origin="тест")


async def test_a_hull_whose_crew_died_is_brought_home_from_the_ground(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The hole D-242 exists for: nobody alive aboard, no edges, no way to order.

    Before the ground console this hull hung with its cargo for ever -- the one
    trap a ship could still make, and this world does not build those (P6).
    """
    home = await _port(session, name="Космодром столицы")
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)

    owner.node_id = connector.id
    await session.flush()
    await ship.ascend(session, constants, catalog, owner, vessel)
    #: The crew is gone: the owner is back on the ground, printed anew.
    owner.node_id = home.id
    await session.flush()

    #: From bare ground the hull is deaf, exactly as before.
    with pytest.raises(ship.NotAboard):
        await ship.recall(session, constants, catalog, owner, vessel)

    await _ground_console(session, home)
    job = await ship.recall(session, constants, catalog, owner, vessel)
    assert job is not None, "с наземной консоли приказ проходит"


async def test_a_hull_without_a_bridge_hears_nothing_from_the_ground(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The ground console talks to the ship's console: no bridge, no order (D-242).

    That is what keeps the bridge worth building after the ground one exists.
    """
    home = await _port(session, name="Космодром столицы")
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    connector = await session.get(Node, vessel.connector_node_id)
    #: Everything a passage needs **except** a console aboard.
    await _equip(session, connector, ENGINE)
    await _equip(session, connector, LIFE)
    await _fuel(session, connector, 200)
    owner.node_id = connector.id
    await session.flush()

    #: Aboard, the missing console is refused as it always was.
    with pytest.raises(ship.NoConsole):
        await ship.ascend(session, constants, catalog, owner, vessel)

    await _equip(session, connector, CONSOLE)
    await ship.ascend(session, constants, catalog, owner, vessel)
    #: Now take the console away and try from the ground.
    yard = await world.node_container(session, connector)
    for thing in await world.contents(session, yard):
        if thing.type_key == CONSOLE:
            await session.delete(thing)
    await session.flush()

    owner.node_id = home.id
    await _ground_console(session, home)
    await session.flush()
    with pytest.raises(ship.Deaf):
        await ship.recall(session, constants, catalog, owner, vessel)


async def test_turning_back_costs_the_way_already_flown(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The way home is as long as the way out has been, and burns its own fuel.

    Shown on a climb, because that is the leg a player takes back most often
    (D-245): "подняться на орбиту" is an order, and an order may be countermanded.
    """
    home = await _port(session, name="Космодром столицы")
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    await _fuel(session, connector, 3000)
    owner.node_id = connector.id
    await session.flush()

    flight = await ship.ascend(session, constants, catalog, owner, vessel)
    assert vessel.left_node_id == home.id, "причал, с которого ушли, запомнен"
    #: `created_at` is the database's own stamp: read it back rather than off
    #: an object that has not seen the row since the insert.
    await session.refresh(flight)

    #: Half a day out. The way back is half a day, to the pier it left. Well
    #: past the landing floor, so what is pinned here is the rule itself.
    gone = timedelta(hours=12)
    moment = flight.created_at + gone
    before = await ship.fuel_aboard(session, vessel)
    back = await ship.recall(session, constants, catalog, owner, vessel, now=moment)

    assert back.payload["to"] == str(home.id)
    assert back.run_at - moment == gone
    assert await ship.fuel_aboard(session, vessel) < before, "разворот сжёг своё топливо"

    await session.refresh(flight)
    assert flight.state is JobState.CANCELLED, "прежний рейс снят: два прихода на один корпус"


async def test_a_turn_back_is_not_turned_back(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Two clicks must not bring a hull home from anywhere, instantly and free.

    A turn-back counts the hours of the passage it replaced. Counted afresh
    from **itself** they are nought: no fuel, no time, and the hull lands at
    home the same second. That is the whole price of a turn-back gone, so the
    second one is refused outright -- the ship is already going there.
    """
    home = await _port(session, name="Космодром столицы")
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    await _fuel(session, connector, 3000)
    owner.node_id = connector.id
    await session.flush()

    flight = await ship.ascend(session, constants, catalog, owner, vessel)
    await session.refresh(flight)
    moment = flight.created_at + timedelta(hours=12)
    back = await ship.recall(session, constants, catalog, owner, vessel, now=moment)

    burnt = await ship.fuel_aboard(session, vessel)
    with pytest.raises(ship.InFlight):
        await ship.recall(
            session, constants, catalog, owner, vessel, now=moment + timedelta(seconds=1)
        )
    assert await ship.fuel_aboard(session, vessel) == burnt, "отказ не сжёг топлива"
    #: And the way home is still the half day it was, not nought.
    await session.refresh(back)
    assert back.run_at - moment == timedelta(hours=12)


async def test_a_turn_back_to_a_pier_without_a_yard_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hull is not sent back to a node with nothing to moor to.

    A landing asks two questions of a destination -- is there a yard, and is the
    beacon lit -- and a turn-back must ask both. It used to ask only the second,
    so a pier whose yard was carried off while the hull flew still took the
    turn-back, and the arrival laid a gangway onto a node with no spaceport at
    all. Written first, dismissed as wrong, and right after all (review of
    D-242).
    """
    home = await _port(session, name="Космодром столицы")
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()

    flight = await ship.ascend(session, constants, catalog, owner, vessel)
    await session.refresh(flight)

    #: The yard is carried off while the hull is under way.
    yard = await world.node_container(session, home)
    for thing in await world.contents(session, yard):
        if thing.type_key == "space_shipyard":
            await session.delete(thing)
    await session.flush()

    with pytest.raises(ship.NoPort):
        await ship.recall(
            session, constants, catalog, owner, vessel, now=flight.created_at + timedelta(hours=1)
        )


async def test_a_turn_back_never_costs_less_than_a_landing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Turned round in the first minute, a hull has gone nowhere -- and still
    has to come down.

    Without a floor the arithmetic put it back on the pier at once and for
    nothing, which is a way to skip the hours every descent costs (D-245): lift,
    turn back, and be down again before the gauge has moved.
    """
    home = await _port(session, name="Космодром столицы")
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()

    flight = await ship.ascend(session, constants, catalog, owner, vessel)
    await session.refresh(flight)

    before = await ship.fuel_aboard(session, vessel)
    #: Turned round the same second it set out.
    back = await ship.recall(session, constants, catalog, owner, vessel, now=flight.created_at)

    thrust_ratio = await ship.ratio(session, constants, catalog, vessel)
    landing = ship.fall_hours(constants, Planet.TERRA, thrust_ratio)
    assert back.run_at - flight.created_at == pytest.approx(
        timedelta(hours=landing), abs=timedelta(seconds=1)
    ), "разворот в ту же секунду всё равно длится посадку"
    assert await ship.fuel_aboard(session, vessel) < before, "и стоит топлива"


async def test_somebody_elses_ground_console_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Orders are given from one's own console, on land one disposes of (D-242)."""
    home = await _port(session, name="Космодром столицы")
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()
    await ship.ascend(session, constants, catalog, owner, vessel)

    #: A console standing on somebody else's plot.
    stranger = await world.create_identity(session, f"Сосед-{uuid.uuid4().hex[:6]}")
    yard_node = await world.create_node(
        session, f"terra.yard.{uuid.uuid4().hex[:8]}", "Чужой двор", area_m2=200
    )
    yard_node.owner_identity_id = stranger.id
    await session.flush()
    await _ground_console(session, yard_node)
    owner.node_id = yard_node.id
    await session.flush()

    with pytest.raises(ship.NotYours):
        await ship.recall(session, constants, catalog, owner, vessel)


async def test_an_arrival_that_fires_twice_moors_once(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hull is docked by exactly one arrival.

    A retry after a failure, or a job that outlived a turn-back, would otherwise
    lay a second gangway and moor a ship that is already moored.
    """
    home = await _port(session, name="Космодром столицы")
    away = await _orbit(session)
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()
    job = await ship.ascend(session, constants, catalog, owner, vessel)

    await ship.arrived(session, job)
    berth, docked = vessel.berth, vessel.docked_node_id
    assert docked == away.id

    await ship.arrived(session, job)
    assert vessel.docked_node_id == docked and vessel.berth == berth
    ways = await travel.exits(session, constants, away)
    assert [way.node_id for way in ways].count(connector.id) == 1, "трап один"


async def test_a_turn_back_to_a_dark_pier_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The same question `fly` asks of a destination (D-232): a hull is not sent
    where it will not be taken.

    A pier on the permafrost works while its node is warm and its yard has
    power. An unpowered one on Aurora is dark, and a hull turning back to it
    would be turning back to nowhere -- so the turn-back is refused and the
    passage it is on stands. Not a chain of failures: the way on is still
    there, and the fuel for it was guaranteed at the casting off.
    """
    #: The planet's own node carries its climate (D-231): the engine reads the
    #: world, not a constant.
    await world.create_node(
        session,
        Planet.AURORA.value,
        "Аврора",
        area_m2=1,
        planet=Planet.AURORA,
        layer=Layer.SPACE,
        properties={frost.FROST: True},
    )
    home = await _port(session, name="Космодром Мерида", planet=Planet.AURORA)
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()

    flight = await ship.ascend(session, constants, catalog, owner, vessel)
    await session.refresh(flight)
    #: No city, no pool, no heat: the pier it left is dark.
    assert not await ship.beacon_lit(session, constants, home)

    with pytest.raises(ship.NoPort):
        await ship.recall(
            session, constants, catalog, owner, vessel, now=flight.created_at + timedelta(hours=1)
        )


async def test_two_turn_backs_in_one_second_burn_one_return(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Two sockets of one player, or an AI citizen (D-224), pressing together.

    The turn-back writes twice -- the passage's job and the tanks -- and both
    writes are worth doubling. The hull's passage is taken under lock before
    anything is decided, so the second order finds a hull already going home.
    """
    async with factory() as session, session.begin():
        home = await _port(session, name="Космодром столицы")
        _, owner = await _shipwright(session, home)
        vessel = await _laid(session, constants, owner, home)
        await _flightworthy(session, constants, catalog, vessel)
        connector = await session.get(Node, vessel.connector_node_id)
        await _fuel(session, connector, 3000)
        owner.node_id = connector.id
        await session.flush()
        flight = await ship.ascend(session, constants, catalog, owner, vessel)
        await session.refresh(flight)
        ship_id, owner_id = vessel.id, owner.id
        flown = 12.0
        moment = flight.created_at + timedelta(hours=flown)
        before = await ship.fuel_aboard(session, vessel)
        #: What one turn-back costs, by the engine's own formula: the hours it
        #: has flown, priced by mass and by the class that pushes it.
        one_turn = ship.fuel_for(
            constants,
            await ship.mass(session, constants, catalog, vessel),
            flown,
            klass=await ship.engine_class(session, constants, vessel),
        )

    ready = asyncio.Barrier(2)

    async def turn() -> str:
        async with factory() as db, db.begin():
            mine = await db.get(Ship, ship_id)
            me = await db.get(Body, owner_id)
            await ready.wait()
            try:
                await ship.recall(db, constants, catalog, me, mine, now=moment)
            #: Whichever refusal the loser gets is the right one, and which it is
            #: depends on where it was standing when the winner committed: the
            #: passage it meant to cancel is gone (`Docked`), or it has already
            #: read the turn-back that replaced it (`InFlight`). What matters is
            #: that the second order changes nothing, and that is asserted below.
            except (ship.Docked, ship.InFlight):
                return "refused"
            return "turned"

    answers = await asyncio.gather(turn(), turn())
    assert sorted(answers) == ["refused", "turned"], f"оба разворота прошли: {answers}"

    async with factory() as session:
        vessel = await session.get(Ship, ship_id)
        left = await ship.fuel_aboard(session, vessel)
        going = (
            (
                await session.execute(
                    select(Job).where(
                        Job.kind == JobKind.SHIP_FLIGHT.value,
                        Job.state == JobState.PENDING,
                        Job.payload["ship"].astext == str(ship_id),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(going) == 1, "на корпусе один рейс, а не два"
    #: Exactly one turn-back's worth, not "at least some": two burns would pass
    #: a `left < before` and hide the very doubling this test is here for.
    assert before - left == pytest.approx(one_turn, abs=0.01), (
        f"списано {before - left:.2f} вместо {one_turn:.2f}"
    )


async def test_a_hull_that_is_not_flying_has_nothing_to_turn_back(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    port = await _port(session)
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()

    with pytest.raises(ship.Docked):
        await ship.recall(session, constants, catalog, owner, vessel)


# --- the orbital step (D-245) -------------------------------------------------


async def test_the_way_between_worlds_goes_orbit_to_orbit(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Космодром на Терре -> орбита Терры -> орбита Авроры -> космодром на Авроре.

    The whole of D-245 in one journey. What it pins is that each leg exists and
    that none of them may be skipped: the ground does not cross to another
    world, an orbit does not take a landing on somebody else's planet, and the
    hull carries the planet it is actually over at every step.
    """
    async with factory() as session, session.begin():
        home = await _port(session, name="Космодром столицы")
        pad = await _port(session, name="Космодром Мерида", planet=Planet.AURORA)
        _, owner = await _shipwright(session, home)
        vessel = await _laid(session, constants, owner, home)
        await _flightworthy(session, constants, catalog, vessel)
        connector = await session.get(Node, vessel.connector_node_id)
        await _fuel(session, connector, 5000)
        owner.node_id = connector.id
        await session.flush()

        aurora = await _orbit(session, Planet.AURORA)
        #: From the pad one may not cross, and one may not land on Aurora.
        with pytest.raises(ship.Docked):
            await ship.fly(session, constants, catalog, owner, vessel, aurora)

        await _in_orbit(session, constants, catalog, owner, vessel)
        #: And from Terra's orbit one may not come down on Aurora either: the
        #: pad is chosen over the planet one is actually above.
        with pytest.raises(ship.TooFar):
            await ship.land(session, constants, catalog, owner, vessel, pad)
        #: Nor is there a crossing to the orbit one is already at.
        with pytest.raises(ship.TooFar):
            await ship.fly(session, constants, catalog, owner, vessel, await _orbit(session))

        crossing = await ship.fly(session, constants, catalog, owner, vessel, aurora)
        term, ship_id, aurora_id, pad_id = crossing.run_at, vessel.id, aurora.id, pad.id

    assert await jobs.run_one(factory, now=term) is not None

    async with factory() as session, session.begin():
        vessel = await session.get(Ship, ship_id)
        assert vessel.docked_node_id == aurora_id, "борт на орбите Авроры"
        connector = await session.get(Node, vessel.connector_node_id)
        assert connector.planet is Planet.AURORA, "и несёт планету, над которой висит"

        owner = await _body_of(session, vessel)
        owner.node_id = connector.id
        await session.flush()
        descent = await ship.land(
            session, constants, catalog, owner, vessel, await session.get(Node, pad_id)
        )
        term = descent.run_at

    assert await jobs.run_one(factory, now=term) is not None

    async with factory() as session:
        vessel = await session.get(Ship, ship_id)
        assert vessel.docked_node_id == pad_id, "борт сел на выбранный космодром"
        assert vessel.berth == 1, "и занял место у причала"


def test_a_heavy_world_costs_more_to_leave(constants: Constants) -> None:
    """Gravity is the first number by which planets differ (D-245).

    Pyroxis is dense and heavy: leaving it is dearest, and that is a reason of
    its own why a watch there goes at the limit. Aurora is light. And coming
    down is always shorter than going up -- the weight one climbed against is
    on the ship's side.
    """
    heavy = ship.climb_hours(constants, Planet.PYROXIS, 1.0)
    home = ship.climb_hours(constants, Planet.TERRA, 1.0)
    light = ship.climb_hours(constants, Planet.AURORA, 1.0)
    assert light < home < heavy, "тяжесть планеты решает, сколько стоит уйти"
    assert ship.fall_hours(constants, Planet.TERRA, 1.0) < home, "спуск дешевле подъёма"
    #: A planet the vault says nothing about weighs what Terra weighs: a missing
    #: line must not make a world free to leave.
    assert ship.gravity(constants, Planet.TERRA) == 1.0


async def test_a_planet_with_no_lit_beacon_is_not_crossed_to(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A world one may reach and never leave the orbit of is a trap (D-232, D-245).

    The crossing is refused at **this** end, while there is still a choice: the
    hull would otherwise hang over Aurora with fuel for a descent and nowhere
    to spend it.
    """
    await world.create_node(
        session,
        Planet.AURORA.value,
        "Аврора",
        area_m2=1,
        planet=Planet.AURORA,
        layer=Layer.SPACE,
        properties={frost.FROST: True},
    )
    home = await _port(session, name="Космодром столицы")
    await _port(session, name="Космодром Мерида", planet=Planet.AURORA)
    dark = await _orbit(session, Planet.AURORA)
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    await _fuel(session, connector, 5000)
    owner.node_id = connector.id
    await session.flush()

    await _in_orbit(session, constants, catalog, owner, vessel)
    #: No city, no power, permafrost: the only pier on the planet is dark.
    assert not await ship.beacon_lit(session, constants, await _port(session, planet=Planet.AURORA))
    with pytest.raises(ship.NoPort):
        await ship.fly(session, constants, catalog, owner, vessel, dark)
    #: And the console does not offer what the engine refuses.
    summary = await ship.profile(session, constants, catalog, vessel)
    assert all(route["planet"] != Planet.AURORA.value for route in summary["routes"])


async def test_an_orbit_is_the_void_whatever_hangs_below_it(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hull in orbit makes its own air, and stepping out is a spacewalk (D-233, D-245).

    The orbital node carries the planet it belongs to, so the naive reading --
    "Terra has air, therefore this node has air" -- would have opened the hatch
    onto vacuum.
    """
    from src.engine import oxygen

    home = await _port(session, name="Космодром столицы")
    orbit = await _orbit(session)
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()

    assert await oxygen.free_air(session, home), "на земле Терры дышат даром"
    assert not await oxygen.sealed(session, vessel), "у причала люк можно и открыть"

    await _in_orbit(session, constants, catalog, owner, vessel)
    assert not await oxygen.free_air(session, orbit), "орбита — пустота"
    assert await oxygen.sealed(session, vessel), "на орбите корпус живёт своим воздухом"
    assert not await oxygen.free_air(session, connector), "и отсек тоже"


async def test_a_turn_back_into_orbit_keeps_the_descent(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The turn-back is a leg, and it keeps what every leg keeps (D-245).

    The way in was short. A crossing keeps back the descent onto the planet it
    is aimed at, and a turn-back counts the hours it has flown -- nought, in
    the first minute. Without a reserve of its own the hull came home to an
    orbit it could not afford to leave: `fall_hours` is the planet's, and the
    reserve it carried was measured against the other one.
    """
    #: Off the heaviest world in the system onto the lightest: Pyroxis costs
    #: `planet.gravity` 1.3 to come down onto and Aurora 0.8, so the reserve the
    #: crossing keeps is worth barely half the descent waiting at the other end
    #: of a turn-back.
    home = await _port(session, name="Плато Наковальни", planet=Planet.PYROXIS)
    await _port(session, name="Космодром Мерида", planet=Planet.AURORA)
    aurora = await _orbit(session, Planet.AURORA)
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    #: Ballast, and it is the point of the test as much as the planets are: a
    #: hull whose mass is mostly fuel lightens as it burns, and the reserve it
    #: measured at the casting off buys more descent than it was sold. A loaded
    #: freighter does not -- its mass is its cargo -- and it is the loaded
    #: freighter the reserve has to be right for.
    await _equip(session, connector, "iron_ingot", amount=1200)
    await _equip(session, connector, ENGINE, amount=40)
    await _fuel(session, connector, 200)
    owner.node_id = connector.id
    await session.flush()

    await _in_orbit(session, constants, catalog, owner, vessel)
    crossing = await ship.fly(session, constants, catalog, owner, vessel, aurora)
    await session.refresh(crossing)

    #: Drained to exactly the descent the crossing kept back, and not a gram
    #: more: that is the state the hole was reachable from.
    thrust_ratio = await ship.ratio(session, constants, catalog, vessel)
    kept = ship.fuel_for(
        constants,
        await ship.mass(session, constants, catalog, vessel),
        ship.fall_hours(constants, Planet.AURORA, thrust_ratio),
        klass=await ship.engine_class(session, constants, vessel),
    )
    await ship._spend(
        session,
        await ship.fuel_stacks(session, vessel),
        await ship.fuel_aboard(session, vessel) - kept,
    )
    await session.flush()

    with pytest.raises(ship.NoFuel):
        await ship.recall(session, constants, catalog, owner, vessel, now=crossing.created_at)
    #: And the hull goes on to Aurora, where the fuel it holds is exactly the
    #: descent it was promised.
    await session.refresh(crossing)
    assert crossing.state is JobState.PENDING, "отказ не снял рейс"


async def test_a_descent_is_not_aimed_at_the_orbit_itself(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """An orbit is not a pad (D-245).

    `_will_take` says yes to every orbital node -- space needs no yard and has
    no beacon -- so a descent aimed at the very orbit the hull is moored to
    passed every check: the trap came off, a descent was charged, and the hull
    moored again where it already was, one leg's fuel poorer and below the
    reserve that keeps an orbit leavable.
    """
    home = await _port(session, name="Космодром столицы")
    orbit = await _orbit(session)
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()

    await _in_orbit(session, constants, catalog, owner, vessel)
    before = await ship.fuel_aboard(session, vessel)
    with pytest.raises(ship.NoPort):
        await ship.land(session, constants, catalog, owner, vessel, orbit)
    assert vessel.docked_node_id == orbit.id, "корабль остался там, где стоял"
    assert await ship.fuel_aboard(session, vessel) == before, "отказ не сжёг топлива"


async def test_an_orbit_has_no_pier_to_queue_at(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Hulls hang beside one another over a planet, and the walk out is the same
    short spacewalk however many are parked (D-245).

    Numbered berths would have made the twentieth hull over Terra climb a
    gangway twenty times the first one's, for a pier that does not exist.
    """
    home = await _port(session, name="Космодром столицы")
    parked = []
    for number in range(3):
        _, owner = await _shipwright(session, home)
        vessel = await _laid(session, constants, owner, home, name=f"Борт-{number}")
        await _flightworthy(session, constants, catalog, vessel)
        connector = await session.get(Node, vessel.connector_node_id)
        owner.node_id = connector.id
        await session.flush()
        parked.append(await _in_orbit(session, constants, catalog, owner, vessel))

    assert [vessel.berth for vessel in parked] == [1, 1, 1], "на орбите причала нет"
