# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The crossing between worlds (D-271, D-289): the slider, the order, the turn-back.

No route is closed by class; the hull carries the planet it flew to and
prices the way home from there; the slider has two ends and the order
names one, and nothing is burnt at the order; a turn-back is a new order
home; the departure burn is refused without fuel, the arrival is a warning.
The legs live in `test_ship_flight.py`, the sky itself in `test_ship_sky.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ship_kit import (
    CONSOLE,
    LIFE,
    _equip,
    _fast_sample,
    _flightworthy,
    _flown,
    _fuel,
    _in_orbit,
    _laid,
    _orbit,
    _port,
    _shipwright,
)
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import ship
from src.models.ship import Ship
from src.models.world import Node, Planet

# --- a ship is no short cut across the land -----------------------------------


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
    assert aurora["cheap"]["hours"] > 0 and aurora["cheap"]["fuel"] > 0
    assert await ship.fly(session, constants, catalog, owner, vessel, far) is not None


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
        moment = datetime.now(UTC)
        fast = await _fast_sample(session, constants, catalog, vessel, Planet.AURORA)
        arrives = await ship.fly(
            session, constants, catalog, owner, vessel, far, hours=fast["hours"], now=moment
        )
        #: The interplanetary passage is hours of the sky, not the minutes of
        #: a leg -- and it is flown, not tabled (D-289).
        assert arrives > moment
        ship_id = vessel.id
        home_key = ship.orbit_key(Planet.TERRA)

    async with factory() as session, session.begin():
        vessel = await session.get(Ship, ship_id)
        await _flown(session, constants, catalog, vessel, since=moment, until=arrives)

    async with factory() as session:
        vessel = await session.get(Ship, ship_id)
        connector = await session.get(Node, vessel.connector_node_id)
        assert connector.planet is Planet.AURORA, "корабль стоит там, куда прилетел"

        summary = await ship.profile(session, constants, catalog, vessel)
        back = next(route for route in summary["routes"] if route["node"] == home_key)
        #: The way back is an interplanetary passage, not a local hop: the sky
        #: between the two is what it costs, and it is priced in hours and fuel
        #: rather than in a class of engine somebody must own (D-235).
        assert back["cheap"]["hours"] > 0 and back["cheap"]["fuel"] > 0, (
            "обратный рейс считается межпланетным, а не местным"
        )


async def test_the_slider_has_two_ends_and_the_order_names_one(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The owner picks the flight time; the engines and the tanks bound it (D-271).

    Unnamed, the cheapest arc flies. Named, the arc for those hours is what is
    paid for: more delta-v than the cheapest and more fuel with it. Asked for
    a speed the engines cannot deliver in the time, the order is refused with
    the numbers; asked for an hour off the slider, refused likewise.
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

    forecast = await ship.forecast(session, constants, catalog, vessel, Planet.AURORA)
    samples = forecast["samples"]
    assert samples and any(one["ok"] for one in samples), "хоть одна дуга по силам двигателям"
    fast = next(one for one in samples if one["ok"])
    cheap = min(samples, key=lambda one: one["dv"])
    assert fast["dv"] > cheap["dv"] and fast["fuel"] > cheap["fuel"]
    #: Off the slider on either side: refused before anything is burnt.
    with pytest.raises(ship.NoArc):
        await ship.fly(session, constants, catalog, owner, vessel, far, hours=0)
    with pytest.raises(ship.NoArc):
        await ship.fly(
            session,
            constants,
            catalog,
            owner,
            vessel,
            far,
            hours=constants[R.ORBIT_LONGEST_DAYS] * 24 + 1,
        )
    #: A time the engines cannot make: the first sample that is not `ok`, if
    #: there is one, is refused by thrust and not by fuel.
    slow_engines = [one for one in samples if not one["ok"]]
    if slow_engines:
        with pytest.raises(ship.NotEnoughThrust):
            await ship.fly(
                session, constants, catalog, owner, vessel, far, hours=slow_engines[0]["hours"]
            )
    #: The order is the point of the slider, and nothing is burnt at the
    #: order (D-289): the tanks pay as the engines burn, tick by tick.
    moment = datetime.now(UTC)
    before = await ship.fuel_aboard(session, constants, catalog, vessel)
    arrives = await ship.fly(
        session, constants, catalog, owner, vessel, far, hours=fast["hours"], now=moment
    )
    #: The promised hour is the slider's plus the braking at this thrust:
    #: the plan's burns are instants, the engines' are not.
    assert timedelta(hours=fast["hours"]) <= arrives - moment < timedelta(hours=fast["hours"] + 24)
    assert await ship.fuel_aboard(session, constants, catalog, vessel) == before, (
        "заказ не жжёт топлива: жгут двигатели по ходу"
    )
    assert vessel.course["hours"] == pytest.approx(fast["hours"])
    assert len(vessel.course["trace"]) >= 2, "дуга записана в курс, и карта её рисует"
    #: An hour under way: the departure burn has started, and it is paid for.
    await ship.sim.tick_sky(session, constants, catalog, now=moment + timedelta(hours=1))
    assert await ship.fuel_aboard(session, constants, catalog, vessel) < before, (
        "час пути — и баки легче"
    )


async def test_a_turn_back_from_an_arc_is_a_new_order_home(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The way home is a new order laid from where the hull is (D-242, D-289)."""
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

    moment = datetime.now(UTC)
    fast = await _fast_sample(session, constants, catalog, vessel, Planet.AURORA)
    await ship.fly(session, constants, catalog, owner, vessel, far, hours=fast["hours"], now=moment)
    out = list(vessel.course["trace"])
    #: Two hours out, turned home: not the flown part paid again (D-242's
    #: rule for the tabled passage) but a new order laid from where the hull
    #: is, to where Terra will be (D-289).
    later = moment + timedelta(hours=2)
    await ship.sim.tick_sky(session, constants, catalog, now=later)
    arrives = await ship.recall(session, constants, catalog, owner, vessel, now=later)
    assert arrives > later
    home = vessel.course
    assert home["target"] == ship.orbit_key(Planet.TERRA), (
        "разворот ведёт на орбиту, с которой ушли"
    )
    assert home["back"] is True
    assert home["trace"][0] != out[0], "и начинается там, где корпус сейчас, а не где начал"
    summary = await ship.profile(session, constants, catalog, vessel)
    assert summary["flight"]["back"] is True, "консоль знает, что это путь назад"
    #: A turn-back is not turned back: the hull is already going home.
    with pytest.raises(ship.ShipError):
        await ship.recall(
            session, constants, catalog, owner, vessel, now=later + timedelta(minutes=1)
        )


async def test_a_crossing_needs_more_fuel_than_the_climb(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Fuel goes by mass and by speed: enough to reach orbit is not enough for the fast arc.

    The climb is affordable by construction -- it already checked the fuel for
    the way back down, and that is the whole of a local journey (D-245). What a
    short tank does not buy is the delta-v of the fast end of the slider
    (D-271): the cheap arc of a light hull is cheap indeed, the fast one is not.
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
    #: nowhere near enough for a world away. The passage pays for the arc's
    #: delta-v (D-271): its price depends on where the planets stand, and this
    #: tank is short of even the cheapest arc the sky offers.
    await _fuel(session, connector, 4)
    owner.node_id = connector.id
    await session.flush()

    await _in_orbit(session, constants, catalog, owner, vessel)
    forecast = await ship.forecast(session, constants, catalog, vessel, Planet.AURORA)
    fast = next(one for one in forecast["samples"] if one["ok"])
    #: The console's warning (D-289): the arc and the descent behind it are
    #: more than the tank holds. The engine refuses only what cannot start --
    #: the departure burn -- so the tank is drained to a drop for that.
    assert fast["fuel"] + forecast["reserve"] > await ship.fuel_aboard(
        session, constants, catalog, vessel
    )
    await ship._spend(
        session,
        await ship.fuel_stacks(session, constants, catalog, vessel),
        await ship.fuel_aboard(session, constants, catalog, vessel) - 0.01,
    )
    await session.flush()
    with pytest.raises(ship.NoFuel):
        await ship.fly(session, constants, catalog, owner, vessel, far, hours=fast["hours"])
