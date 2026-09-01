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

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ship_kit import ENGINE, _equip, _laid, _port, _shipwright
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import jobs, occupation, rest, ship, travel, world
from src.models.estate import Building
from src.models.identity import Body
from src.models.job import JobState
from src.models.world import Node

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
