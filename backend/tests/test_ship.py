# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The slipway: a ship is built node by node (D-230).

A foundation gives a node with an edge to the port and is written off; the
keel is the body's own work, laid by the worker; the hull grows a node at a
time behind one connector, and every node is both a place and mass priced
by the vault. Flying lives in `test_ship_flight.py`, the console and the
way home in `test_ship_console.py`, other worlds in `test_ship_orbits.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ship_kit import ENGINE, _equip, _laid, _orbit, _port, _shipwright
from src import globe
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import gear, jobs, occupation, places, rest, ship, station, storage, travel, world
from src.engine.ship._base import FOUNDATION
from src.models.estate import Building
from src.models.identity import Body
from src.models.job import JobState
from src.models.world import Node, Planet

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


async def test_a_second_kind_of_foundation_is_data_and_lays_the_same_node(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Any member of the class lays a node -- a second foundation needs no code (D-325).

    The rule is D-215's: behaviour binds to a class, never to an item name.
    It is worth a test of its own because the vault leaned on it — Pyroxis has
    no heat-shield tiles, so a hull there is made of pyroxite, and the way the
    ladder closed that dead end was a **second recipe** of the same class
    rather than a class in the ingredients (which the engine does not close).
    If this rule ever narrows to the first member, that closure goes back to
    being a dead end and nothing else says so.

    Named through `of_class` rather than by the pyroxite key: what is asserted
    is that **every** member lays a node, so the day a third one is written it
    is covered without touching this test.
    """
    members = catalog.recipes.of_class(FOUNDATION)
    assert len(members) > 1, "класс основы держит больше одного члена — иначе правило спит"

    for member in members:
        port = await _port(session)
        identity = await world.create_identity(session, f"Корабел-{member}")
        body = await world.print_body(session, identity, port)
        pocket = await world.body_container(session, body)
        await world.grant_item(session, pocket, member, origin="тест")

        vessel = await _laid(session, constants, body, port, name=f"Заря-{member}")
        assert len(await ship.nodes_of(session, vessel)) == 1, member
        #: And written off: a foundation is a consumable, whichever member
        #: of the class it happens to be.
        assert not await ship._foundation_at_hand(session, body), f"«{member}» ушла в закладку"


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

    Laying a foundation onto a hull would be a second ship welded to the
    first for good.
    """
    port = await _port(session)
    _, body = await _shipwright(session, port, foundations=2)
    vessel = await _laid(session, constants, body, port)
    body.node_id = vessel.connector_node_id
    await session.flush()
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

    #: Cargo weighs as well.
    await _equip(session, connector, "iron_ingot", amount=100)
    assert await ship.mass(session, constants, catalog, vessel) > 2 * constants[R.SHIP_NODE_MASS]


async def test_a_chest_aboard_does_not_hide_its_cargo(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Mass hiding inside furniture is mass all the same (D-313).

    A hull flies by thrust over mass, so a chest that weighed only its lid
    would be a free hold: pack the ore into furniture and the passage times
    would believe it.
    """
    port = await _port(session)
    _, body = await _shipwright(session, port)
    vessel = await _laid(session, constants, body, port)
    connector = await session.get(Node, vessel.connector_node_id)

    bare = await ship.mass(session, constants, catalog, vessel)
    chest = await _equip(session, connector, "chest")
    await world.grant_item(
        session,
        await storage.inside(session, chest),
        "iron_ingot",
        amount=100,
        quality=60,
        origin="тест",
    )
    assert await ship.mass(session, constants, catalog, vessel) == pytest.approx(
        bare + gear.mass_of(catalog, "chest", 1) + gear.mass_of(catalog, "iron_ingot", 100)
    )


async def test_a_hull_weighs_all_the_way_down(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One lid deep is not deep enough (D-313).

    A chest inside a chest and a loaded barrow standing in a compartment were
    the two ways left to fly with a free hold: the hull opened exactly one
    layer of storage and never looked into a vehicle at all. Thrust over mass
    is what every passage time comes from, so a lie here reaches the clock.
    """
    from src.engine import storage, transport

    port = await _port(session)
    _, body = await _shipwright(session, port)
    vessel = await _laid(session, constants, body, port)
    connector = await session.get(Node, vessel.connector_node_id)
    bare = await ship.mass(session, constants, catalog, vessel)

    #: A chest in a chest: the hull used to see two lids and no ore.
    outer = await _equip(session, connector, "chest")
    inner = await world.grant_item(
        session, await storage.inside(session, outer), "chest", quality=60, origin="тест"
    )
    await world.grant_item(
        session,
        await storage.inside(session, inner),
        "iron_ingot",
        amount=40,
        quality=60,
        origin="тест",
    )
    #: A loaded barrow: a vehicle is not placeable, so it lies aboard as cargo
    #: -- and its hold was never opened by anybody.
    barrow = await _equip(session, connector, "wheelbarrow")
    await world.grant_item(
        session,
        await transport.cargo(session, barrow),
        "iron_ingot",
        amount=60,
        quality=60,
        origin="тест",
    )

    lids = (
        gear.mass_of(catalog, "chest", 2)
        + gear.mass_of(catalog, "wheelbarrow", 1)
        + gear.mass_of(catalog, "iron_ingot", 100)
    )
    assert await ship.mass(session, constants, catalog, vessel) == pytest.approx(bare + lids)
    #: And the split says the same: what hides in a box is cargo, not machinery.
    parts = await ship.mass_parts(session, constants, catalog, vessel)
    assert parts["hull"] + parts["machines"] + parts["cargo"] == pytest.approx(bare + lids)


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


async def test_an_engine_lying_aboard_neither_pushes_nor_sets_the_class(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """An engine is a machine that stands (D-202, D-278); one that lies is cargo.

    It weighs and it does not push. Counted, it let a hull carry engines in
    its hold past the machines its rooms seat (D-106), none of them on a fuel
    line (D-288): thrust drinking nothing, and a class set by a crate.
    """
    port = await _port(session)
    _, body = await _shipwright(session, port)
    vessel = await _laid(session, constants, body, port)
    connector = await session.get(Node, vessel.connector_node_id)
    body.node_id = connector.id
    await session.flush()
    bare = await ship.mass(session, constants, catalog, vessel)

    #: Taken down by the owner's own door: it lies where it stood.
    engine = await _equip(session, connector, ENGINE)
    await station.take(session, catalog, body, engine)
    assert not engine.installed
    #: And one packed in a chest is cargo of cargo.
    chest = await _equip(session, connector, "chest")
    await world.grant_item(
        session, await storage.inside(session, chest), ENGINE, quality=60, origin="тест"
    )

    assert await ship.thrust(session, constants, vessel) == 0
    assert await ship.engine_class(session, constants, vessel) is None
    assert await ship.engines(session, constants, vessel) == [], "в рубке лежащих двигателей нет"
    assert await ship.mass(session, constants, catalog, vessel) == pytest.approx(
        bare + gear.mass_of(catalog, "chest", 1) + gear.mass_of(catalog, ENGINE, 2)
    ), "груз всё равно весит"
    #: And the console's split calls it cargo, not a station: the chest stands.
    parts = await ship.mass_parts(session, constants, catalog, vessel)
    assert parts["machines"] == pytest.approx(gear.mass_of(catalog, "chest", 1))

    #: Stood up again, the same engine pushes and sets the class.
    await station.place(session, catalog, body, engine)
    assert await ship.thrust(session, constants, vessel) == pytest.approx(
        constants[R.SHIP_THRUST][ENGINE]
    )
    assert await ship.engine_class(session, constants, vessel) == 1
    assert [row["name"] for row in await ship.engines(session, constants, vessel)] == [ENGINE]
    parts = await ship.mass_parts(session, constants, catalog, vessel)
    assert parts["machines"] == pytest.approx(
        gear.mass_of(catalog, "chest", 1) + gear.mass_of(catalog, ENGINE, 1)
    )


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


async def test_a_moored_hull_stands_beside_its_pier_on_the_map(
    session: AsyncSession, constants: Constants
) -> None:
    """The hull a crew boards has a place, and it is next to the port's own.

    The client draws by the place and by nothing else (D-319): a hull sent
    without one was on the wire and not on the map, so the gangway in `exits`
    had nothing to click and nobody could walk aboard. The place is the pier's,
    stepped out by the berth -- near enough to read as that port's, far enough
    not to sit under the port's own mark.
    """
    port = await _port(session)
    _, body = await _shipwright(session, port)
    vessel = await _laid(session, constants, body, port)

    seen = await ship.in_sight(session, constants, port)
    assert seen is not None, "с причала виден пришвартованный корпус"
    hull = next(one for one in seen["nodes"] if one["name"] == vessel.name)
    place = hull["place"]
    assert place is not None and "lat" in place, "у корпуса есть место на сфере"

    pier = places.geo_of(port)
    assert pier is not None
    apart = globe.distance_m(
        globe.radius_m(constants, port.planet), pier, (place["lat"], place["lon"])
    )
    #: Beside the pier, not under its mark and not across the city: the rule
    #: for how far is `places.beside`, and this is only what the map needs of
    #: it -- a point of its own, within a step of the pier's.
    assert 0 < apart <= float(constants[R.MAP_CITY_STEP_M])

    #: And a second hull at the same pier gets a point of its own: on a world
    #: one lands anywhere on both are berth one (D-233), so what tells them
    #: apart is the hull, not the berth.
    _, mate = await _shipwright(session, port)
    other = await _laid(session, constants, mate, port, name="Вечер")
    seen = await ship.in_sight(session, constants, port)
    assert seen is not None
    points = {(one["place"]["lat"], one["place"]["lon"]) for one in seen["nodes"] if one["place"]}
    assert len(points) == 2, "два корпуса — две точки"
    assert other.name != vessel.name


async def test_a_crew_aboard_a_moored_hull_is_on_the_surface_map(
    session: AsyncSession, constants: Constants
) -> None:
    """From aboard, the hull is a point of the pier's city -- so the crew is.

    A room aboard stands on the ship's own flat map and hangs under the ship's
    delegate, which hangs under the planet. Without the hull itself on the
    pier's level the client could climb from the room to nothing: no "you are
    here" on the surface, no place for the camera to open at, and a frame that
    opened over whatever city came first -- half a world from the pier the
    ship had actually landed at.
    """
    port = await _port(session)
    #: A pier stands in a city, as every seeded one does: that is what makes
    #: the hull a member of the city's scene rather than a point beside it.
    town = await world.create_node(
        session, f"terra.town.{uuid.uuid4().hex[:8]}", "Городок", area_m2=1, planet=Planet.TERRA
    )
    port.parent_id = town.id
    await session.flush()
    _, body = await _shipwright(session, port)
    vessel = await _laid(session, constants, body, port)
    connector = await session.get(Node, vessel.connector_node_id)
    body.node_id = connector.id
    await session.flush()

    seen = await ship.in_sight(session, constants, connector)
    assert seen is not None
    delegate = await session.get(Node, vessel.node_id)
    hull = next((one for one in seen["nodes"] if one["key"] == delegate.key), None)
    assert hull is not None, "корпус виден с борта как точка поверхности"
    assert hull["layer"] == port.layer.value, "на слое причала, не в небе"
    assert hull["parent"] == town.key, "под городом причала"
    assert hull["place"] is not None and "lat" in hull["place"]

    #: The rooms hang under it, so the climb from where one stands ends here.
    room = next(one for one in seen["nodes"] if one["key"] == connector.key)
    assert room["parent"] == delegate.key


async def test_a_hull_off_its_pier_lends_no_place(
    session: AsyncSession, constants: Constants
) -> None:
    """In the sky the hull is the sky's: the pier lends nothing to a ship that
    has cast off, and the crew aboard sees its rooms and no surface point.

    Both ways of being off the ground are the same here. A hull under way has
    no pier at all; a hull on its parking circle is moored to the **orbital**
    node (`flight.arrived`), which is the sky and has no ground under it -- so
    a point beside it would be a point on a planet the hull is not standing
    on, and the sky already draws the hull from the clock (D-289).
    """
    port = await _port(session)
    _, body = await _shipwright(session, port)
    vessel = await _laid(session, constants, body, port)
    connector = await session.get(Node, vessel.connector_node_id)
    delegate = await session.get(Node, vessel.node_id)

    orbit = await _orbit(session)
    for pier in (None, orbit.id):
        vessel.docked_node_id = pier
        await session.flush()
        assert await ship.in_sight(session, constants, port) is None, "у причала никого"
        seen = await ship.in_sight(session, constants, connector)
        assert seen is not None
        assert all(one["key"] != delegate.key for one in seen["nodes"]), (
            "корпуса на поверхности нет"
        )


async def test_aboard_one_is_told_whether_the_hull_is_off_its_pier(
    session: AsyncSession, constants: Constants
) -> None:
    """The rooms carry no pier and no orbit, and the hull leaves the answer
    once it casts off -- so the answer says it in one word (D-333, D-225):
    off the pier under way or adrift, moored at a pier or on the circle.
    From the pier there is no such word: the hull is in sight itself."""
    port = await _port(session)
    _, body = await _shipwright(session, port)
    vessel = await _laid(session, constants, body, port)
    connector = await session.get(Node, vessel.connector_node_id)
    body.node_id = connector.id
    await session.flush()

    seen = await ship.in_sight(session, constants, connector)
    assert seen is not None and seen["underway"] is False, "у причала — стоит"
    from_pier = await ship.in_sight(session, constants, port)
    assert from_pier is not None and "underway" not in from_pier

    orbit = await _orbit(session)
    for pier, off in ((None, True), (orbit.id, False)):
        vessel.docked_node_id = pier
        await session.flush()
        seen = await ship.in_sight(session, constants, connector)
        assert seen is not None and seen["underway"] is off, f"pier={pier}"

    vessel.docked_node_id = None
    vessel.lost_at = datetime.now(UTC)
    await session.flush()
    seen = await ship.in_sight(session, constants, connector)
    assert seen is not None and seen["underway"] is False, "потерянный не летит"
