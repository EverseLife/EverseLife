# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Flying: the climb, the mooring and the price of a passage (D-230, D-233).

Docking leaves the land's measurements alone and the climb takes the edge
with it; an overloaded hull or a crew beyond life support does not fly, and
neither does a ship without the fuel to come back; a landing moors at the
chosen pad, berths are numbered, and the summary names the price before
the attempt; a pad takes as many hulls as fit on its ground, and two hulls
do not share the last place (D-319). The slipway lives in `test_ship.py`,
the console in `test_ship_console.py`.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
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
    _orbiting,
    _planet,
    _port,
    _shipwright,
)
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import estate, jobs, ship, station, travel, world
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

    #: Measured by the tick, as everywhere: a read works the distance out but
    #: writes nothing (`estate.measure_cities`, `test_reads`), and what this
    #: test is about is the written number surviving the ship's departure.
    await estate.measure_cities(session)
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


async def test_an_engine_taken_down_does_not_lift_the_hull(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A lying engine is cargo (D-278): the climb reads no thrust from it."""
    port = await _port(session)
    _, body = await _shipwright(session, port)
    vessel = await _laid(session, constants, body, port)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    body.node_id = connector.id
    await session.flush()

    (engine,) = await ship.engines_aboard(session, constants, vessel)
    await station.take(session, catalog, body, engine)
    with pytest.raises(ship.NotEnoughThrust) as refused:
        await ship.ascend(session, constants, catalog, body, vessel)
    assert refused.value.key == "ship-not-enough-thrust"
    assert vessel.docked_node_id == port.id, "корабль без стоящего двигателя остался в порту"


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

        fuel_before = await ship.fuel_aboard(session, constants, catalog, vessel)
        await _in_orbit(session, constants, catalog, owner, vessel)
        assert await _orbiting(session, constants, vessel) == "terra", "борт на орбите"
        flight = await ship.land(session, constants, catalog, owner, vessel, there)
        assert await ship.fuel_aboard(session, constants, catalog, vessel) < fuel_before, (
            "рейс сжёг топливо"
        )
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


async def _hull_in_orbit(
    session: AsyncSession, constants: Constants, catalog: Catalog, home: Node
) -> tuple[Body, Ship]:
    """A flightworthy hull of its own owner, already hanging over the planet."""
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()
    await _in_orbit(session, constants, catalog, owner, vessel)
    return owner, vessel


async def test_a_pad_takes_as_many_hulls_as_fit_on_its_ground(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """A hull sets down on the pad's open ground the way a house stands on its
    plot (D-319): a port with room for one hull takes one.

    Refused **at the choice**, while the hull is still in orbit -- the beacon's
    rule (D-245) -- and the ground is spoken for from the order, not from the
    arrival: the second hull is refused while the first is still in the air,
    and again once it is down. Casting off frees the ground.
    """
    async with factory() as session, session.begin():
        home = await _port(session, name="Космодром столицы")
        pad = await _port(session, name="Тесный космодром")
        first_owner, first = await _hull_in_orbit(session, constants, catalog, home)
        second_owner, second = await _hull_in_orbit(session, constants, catalog, home)
        need = await ship.hull_footprint(session, first)
        assert need == constants[R.SHIP_NODE_AREA] * 1, "корпус в один узел -- одна площадь узла"
        #: Ground for exactly one hull: the yard's roof, and one hull's worth of apron.
        pad.area_m2 = 80 + need
        await session.flush()
        assert await estate.free_ground(session, pad) == need

        flight = await ship.land(session, constants, catalog, first_owner, first, pad)
        assert await estate.free_ground(session, pad) == 0, "спуск занял землю с приказа"
        with pytest.raises(ship.NoPort) as refused:
            await ship.land(session, constants, catalog, second_owner, second, pad)
        assert refused.value.key == "ship-no-room"
        assert refused.value.params["room"] == 0 and refused.value.params["need"] == round(need)
        term = flight.run_at
        pad_id, first_id, second_id = pad.id, first.id, second.id
        first_owner_id, second_owner_id = first_owner.id, second_owner.id

    assert await jobs.run_one(factory, now=term) is not None

    async with factory() as session, session.begin():
        first = await session.get(Ship, first_id)
        pad = await session.get(Node, pad_id)
        assert first.docked_node_id == pad_id
        assert await estate.free_ground(session, pad) == 0, "севший корпус стоит на земле"
        second_owner = await session.get(Body, second_owner_id)
        second = await session.get(Ship, second_id)
        with pytest.raises(ship.NoPort):
            await ship.land(session, constants, catalog, second_owner, second, pad)
        #: The first lifts off: its ground is free again, and the second may come down.
        first_owner = await session.get(Body, first_owner_id)
        await ship.ascend(session, constants, catalog, first_owner, first)
        assert await estate.free_ground(session, pad) == need, "улетевший корпус землю освободил"
        await ship.land(session, constants, catalog, second_owner, second, pad)


async def test_two_hulls_do_not_share_the_last_place(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two crews choose the same pad in the same second, and it has room for one.

    The pad's row is held while the order is decided, so the second waits at
    the lock and reads the first's descent among the hulls on their way down.
    Without it both read the last place, both burn the fuel, and two hulls
    arrive to ground for one.
    """
    async with factory() as session, session.begin():
        home = await _port(session, name="Космодром столицы")
        pad = await _port(session, name="Тесный космодром")
        first_owner, first = await _hull_in_orbit(session, constants, catalog, home)
        second_owner, second = await _hull_in_orbit(session, constants, catalog, home)
        pad.area_m2 = 80 + await ship.hull_footprint(session, first)
        await session.flush()
        pad_id = pad.id
        crews = [(first_owner.id, first.id), (second_owner.id, second.id)]

    _slow(monkeypatch, estate, "free_ground")
    ready = asyncio.Barrier(2)

    async def order(owner_id: uuid.UUID, ship_id: uuid.UUID) -> str:
        async with factory() as db, db.begin():
            me = await db.get(Body, owner_id)
            mine = await db.get(Ship, ship_id)
            pad = await db.get(Node, pad_id)
            await ready.wait()
            try:
                await ship.land(db, constants, catalog, me, mine, pad)
            except ship.NoPort as refusal:
                assert refusal.key == "ship-no-room"
                return "refused"
            return "descends"

    answers = await asyncio.gather(*(order(*crew) for crew in crews))
    assert sorted(answers) == ["descends", "refused"], f"оба спуска прошли: {answers}"

    async with factory() as session:
        legs = (
            (
                await session.execute(
                    select(Job).where(
                        Job.kind == JobKind.SHIP_FLIGHT.value,
                        Job.payload["to"].astext == str(pad_id),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(legs) == 1, "на последнее место идут два корпуса"


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
    burnt = await ship.fuel_aboard(session, constants, catalog, vessel)

    with pytest.raises(ship.InFlight):
        await ship.land(session, constants, catalog, owner, vessel, elsewhere)
    with pytest.raises(ship.InFlight):
        await ship.ascend(session, constants, catalog, owner, vessel)
    assert await ship.fuel_aboard(session, constants, catalog, vessel) == burnt, (
        "отказ всё равно сжёг топливо"
    )


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
        fuel_before = await ship.fuel_aboard(session, constants, catalog, vessel)

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
        spent = fuel_before - await ship.fuel_aboard(session, constants, catalog, vessel)
        assert spent > 0, "рейс не сжёг топлива"


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


async def test_passage_price_follows_the_sky(session: AsyncSession, constants: Constants) -> None:
    """The same passage costs differently at different hours (D-037, D-271).

    Two planets on Keplerian orbits: the inner one laps the outer, and the
    cheapest arc the sky offers changes with where the two stand. At the
    window the cheap end of the slider is the Hohmann transfer -- half the
    period of the ellipse touching both orbits; away from it every arc costs
    more. And the curve is a curve: the fast end always costs more than the
    cheap one.
    """
    inner = await _sphere(session, "terra", Planet.TERRA, radius=100, period=10)
    await _sphere(session, "aurora", Planet.AURORA, radius=100 * 4 ** (2 / 3), period=40)
    await session.flush()

    origin = await world.epoch(session)
    assert origin is not None
    mu = ship.course.mu_of((100.0, 10.0, 0.0))
    hohmann_days = math.pi * math.sqrt(((100 + 100 * 4 ** (2 / 3)) / 2) ** 3 / mu)

    cheapest: list[tuple[float, float]] = []
    for day in range(0, 14):
        curve = await ship.passage_curve(
            session, constants, Planet.TERRA, Planet.AURORA, at=origin + timedelta(days=day)
        )
        assert curve, "небо всегда предлагает хоть одну дугу"
        low = ship.course.cheapest(curve)
        assert low is not None
        assert curve[0].dv > low.dv, "быстрый край дороже дешёвого"
        cheapest.append((low.dv, low.hours))

    best = min(cheapest)
    worst = max(cheapest)
    assert worst[0] > best[0] * 1.5, "в плохой день дешёвая дуга заметно дороже, чем в окно"
    assert best[1] / 24 == pytest.approx(hohmann_days, rel=0.15), (
        "в окно дешёвая дуга -- гомановская, половина периода переходного эллипса"
    )
    assert inner.planet is Planet.TERRA


async def test_corridors_forecast_the_coming_days(
    session: AsyncSession, constants: Constants
) -> None:
    """The map's corridors carry a calendar (D-271): the cheapest passage for
    each of the coming days, one entry per day from today, for every pair of
    playable worlds -- a deferred one is bent round, not flown to."""
    await _sphere(session, "terra", Planet.TERRA, radius=100, period=10)
    await _sphere(session, "aurora", Planet.AURORA, radius=100 * 4 ** (2 / 3), period=40)
    shut = await _sphere(session, "aquatica", Planet.AQUATICA, radius=100 * 2 ** (2 / 3), period=20)
    shut.properties = {**(shut.properties or {}), world.DEFERRED: True}
    await session.flush()
    origin = await world.epoch(session)
    assert origin is not None
    lines = await ship.corridors(session, constants, at=origin + timedelta(days=3, hours=5))
    assert [(line["a"], line["b"]) for line in lines] == [("aurora", "terra")]
    days = lines[0]["days"]
    assert len(days) == int(constants[R.ORBIT_CALENDAR_DAYS])
    assert [day["day"] for day in days] == list(range(3, 3 + len(days)))
    assert all(day["dv"] > 0 and day["hours"] > 0 for day in days)


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
        await ship.fly(session, constants, catalog, owner, vessel, await _planet(session))
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
    assert climb["planet"] == Planet.TERRA.value
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
    #: its room -- the free ground the globe sizes its mark by (D-319) -- and
    #: in nothing the console could charge for.
    assert all(set(pad) <= {"node", "name", "anywhere", "room"} for pad in aloft["landings"])
    assert all(pad["room"] >= 0 for pad in aloft["landings"])
    #: And what the hull needs of that room, once beside the list.
    assert aloft["footprint"] == round(await ship.hull_footprint(session, vessel))
    down = aloft["descent"]
    assert down["hours"] > 0 and down["reachable"]
    #: The ground is the one place a hull may stand with dry tanks: nothing is
    #: kept back from a descent.
    assert down["needs"] == down["fuel"]
