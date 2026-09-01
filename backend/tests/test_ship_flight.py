# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Flying: the climb, the mooring and the price of a passage (D-230, D-233).

Docking leaves the land's measurements alone and the climb takes the edge
with it; an overloaded hull or a crew beyond life support does not fly, and
neither does a ship without the fuel to come back; a landing moors at the
chosen pad, berths are numbered, and the summary names the price before
the attempt. The slipway lives in `test_ship.py`, the console in
`test_ship_console.py`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ship_kit import (
    CONSOLE,
    ENGINE,
    LIFE,
    _body_of,
    _equip,
    _flightworthy,
    _fuel,
    _in_orbit,
    _laid,
    _orbit,
    _port,
    _shipwright,
)
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import jobs, ship, travel, world
from src.models.identity import Body
from src.models.job import Job, JobKind
from src.models.ship import Ship
from src.models.world import Layer, Node, Planet, Surface

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
