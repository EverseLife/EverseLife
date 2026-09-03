# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Between worlds: orbits, beacons and what burns (D-230, D-252).

The way between worlds goes orbit to orbit and a heavy world costs more to
leave; a planet with no lit beacon is not crossed to, an orbit is the void
with no pier to queue at, and a turn back into orbit keeps the descent.
Kerosene closes more of the spend per unit, mixed tanks pay stack by stack,
and two burns never spend the same fuel. The flight nearer the ground
lives in `test_ship_flight.py`.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ship_kit import (
    ENGINE,
    FUEL,
    TANK,
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
from src.engine import frost, jobs, ship, storage, world
from src.models.job import JobState
from src.models.ship import Ship
from src.models.world import Layer, Node, Planet
from src.units import amount_float

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
        await ship.fuel_stacks(session, constants, catalog, vessel),
        await ship.fuel_aboard(session, constants, catalog, vessel) - kept,
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
    before = await ship.fuel_aboard(session, constants, catalog, vessel)
    with pytest.raises(ship.NoPort):
        await ship.land(session, constants, catalog, owner, vessel, orbit)
    assert vessel.docked_node_id == orbit.id, "корабль остался там, где стоял"
    assert await ship.fuel_aboard(session, constants, catalog, vessel) == before, (
        "отказ не сжёг топлива"
    )


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


# --- the kind of fuel (D-252) ------------------------------------------------


async def test_kerosene_closes_more_of_the_spend_per_unit(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The spend is quoted in reference units; the tanks pay by density.

    A unit of kerosene fuel is worth `ship.fuel_energy` reference units, so
    the same tank flies further -- the whole reason a second fuel exists at
    all (D-223: no behaviour, no thing)."""
    worth = constants[R.SHIP_FUEL_ENERGY]
    assert worth["kerosene_fuel"] > worth["rocket_fuel"], "керосин плотнее эталона"

    port = await _port(session)
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    connector = await session.get(Node, vessel.connector_node_id)
    tank = await _equip(session, connector, TANK)
    inside = await storage.inside(session, tank)
    await world.grant_item(session, inside, "kerosene_fuel", amount=100, quality=60, origin="тест")

    #: A hundred units of kerosene answer for 125 reference units...
    assert await ship.fuel_worth(session, constants, catalog, vessel) == pytest.approx(
        100 * worth["kerosene_fuel"]
    )
    #: ...while the console still shows the hundred that has mass (D-230).
    assert await ship.fuel_aboard(session, constants, catalog, vessel) == pytest.approx(100)

    #: Burning 50 reference units costs 40 physical ones: 50 / 1.25.
    burnt = await ship.spend_fuel(session, constants, catalog, vessel, 50)
    assert burnt == pytest.approx(50 / worth["kerosene_fuel"])
    assert await ship.fuel_aboard(session, constants, catalog, vessel) == pytest.approx(100 - burnt)


async def test_mixed_tanks_pay_stack_by_stack(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Rocket fuel beside kerosene in one tank: each stack pays at its own
    worth, and the total energy drawn is exactly what was asked."""
    port = await _port(session)
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    connector = await session.get(Node, vessel.connector_node_id)
    tank = await _equip(session, connector, TANK)
    inside = await storage.inside(session, tank)
    await world.grant_item(session, inside, "rocket_fuel", amount=30, quality=60, origin="тест")
    await world.grant_item(session, inside, "kerosene_fuel", amount=100, quality=60, origin="тест")

    worth = constants[R.SHIP_FUEL_ENERGY]
    before = await ship.fuel_worth(session, constants, catalog, vessel)
    #: More than either stack alone holds in units: the spend crosses kinds.
    #: Stack order is by id -- a uuid, so either kind may pay first; what the
    #: mechanic promises is the energy, not the order.
    burnt = await ship.spend_fuel(session, constants, catalog, vessel, 40)
    left = await ship.fuel_worth(session, constants, catalog, vessel)
    assert before - left == pytest.approx(40, abs=0.01), (
        "снято ровно столько энергии, сколько запрошено"
    )
    stacks = {
        stack.type_key: amount_float(stack.amount)
        for stack in await ship.fuel_stacks(session, constants, catalog, vessel)
    }
    spent_rocket = 30 - stacks.get("rocket_fuel", 0.0)
    spent_kerosene = 100 - stacks.get("kerosene_fuel", 0.0)
    assert burnt == pytest.approx(spent_rocket + spent_kerosene, abs=0.01), (
        "сожжённые единицы — ровно то, что ушло из стеков"
    )
    assert spent_rocket * worth["rocket_fuel"] + spent_kerosene * worth[
        "kerosene_fuel"
    ] == pytest.approx(40, abs=0.01), "каждый стек платил по своей плотности"


async def test_two_burns_do_not_spend_the_same_fuel(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The check and the burn share one lock (D-252): two legs asking the
    same tank must not both pass the check and fly on one hundred units of
    fuel twice. The second waits at the lock and sees what the first left."""
    async with factory() as session, session.begin():
        port = await _port(session)
        _, owner = await _shipwright(session, port)
        vessel = await _laid(session, constants, owner, port)
        connector = await session.get(Node, vessel.connector_node_id)
        tank = await _equip(session, connector, TANK)
        inside = await storage.inside(session, tank)
        await world.grant_item(session, inside, FUEL, amount=100, quality=60, origin="тест")
        ship_id = vessel.id

    async def leg() -> tuple[float, float]:
        async with factory() as db, db.begin():
            own = await db.get(Ship, ship_id)
            return await ship.burn_checked(db, constants, catalog, own, need=70, whole=70)

    outcomes = await asyncio.gather(leg(), leg())
    burnt = sorted(b for b, _ in outcomes)
    #: One leg flew, the other was told the truth -- 30 left is not 70.
    assert burnt == [pytest.approx(0.0), pytest.approx(70.0)], outcomes
    refused_saw = next(worth for b, worth in outcomes if b == 0)
    assert refused_saw == pytest.approx(30.0), "отказ увидел остаток, а не снимок до чужого рейса"

    async with factory() as db:
        vessel = await db.get(Ship, ship_id)
        assert await ship.fuel_aboard(db, constants, catalog, vessel) == pytest.approx(30.0), (
            "сто единиц минус один рейс: топливо не сгорело дважды"
        )
